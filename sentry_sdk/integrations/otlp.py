from sentry_sdk import capture_event, get_client
from sentry_sdk.consts import VERSION, EndpointType
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import register_external_propagation_context
from sentry_sdk.tracing import (
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
)
from sentry_sdk.tracing_utils import Baggage, extract_sentrytrace_data
from sentry_sdk.utils import (
    Dsn,
    capture_internal_exceptions,
    event_from_exception,
    logger,
)

try:
    from opentelemetry import trace
    from opentelemetry.context import (
        Context,
        create_key,
        get_current,
        get_value,
        set_value,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.textmap import (
        CarrierT,
        Getter,
        Setter,
        TextMapPropagator,
        default_getter,
        default_setter,
    )
    from opentelemetry.sdk.trace import Span, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import (
        INVALID_SPAN_ID,
        INVALID_TRACE_ID,
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        format_span_id,
        format_trace_id,
        get_current_span,
        get_tracer_provider,
        set_tracer_provider,
    )
except ImportError:
    raise DidNotEnable("opentelemetry-distro[otlp] is not installed")


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Set, Tuple


SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")
SENTRY_TRACE_KEY = create_key("sentry-trace")


def otel_propagation_context() -> "Optional[Tuple[str, str]]":
    """
    Get the (trace_id, span_id) from opentelemetry if exists.
    """
    ctx = get_current_span().get_span_context()

    if ctx.trace_id == INVALID_TRACE_ID or ctx.span_id == INVALID_SPAN_ID:
        return None

    return (format_trace_id(ctx.trace_id), format_span_id(ctx.span_id))


def setup_otlp_traces_exporter(
    dsn: "Optional[str]" = None, collector_url: "Optional[str]" = None
) -> None:
    tracer_provider = get_tracer_provider()

    if not isinstance(tracer_provider, TracerProvider):
        logger.debug("[OTLP] No TracerProvider configured by user, creating a new one")
        tracer_provider = TracerProvider()
        set_tracer_provider(tracer_provider)

    endpoint = None
    headers = None
    if collector_url:
        endpoint = collector_url
        logger.debug(f"[OTLP] Sending traces to collector at {endpoint}")
    elif dsn:
        auth = Dsn(dsn).to_auth(f"sentry.python/{VERSION}")
        endpoint = auth.get_api_url(EndpointType.OTLP_TRACES)
        headers = {"X-Sentry-Auth": auth.to_header()}
        logger.debug(f"[OTLP] Sending traces to {endpoint}")

    otlp_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)


_sentry_patched_exception = False


def setup_capture_exceptions() -> None:
    """
    Intercept otel's Span.record_exception to automatically capture those exceptions in Sentry.
    """
    global _sentry_patched_exception
    _original_record_exception = Span.record_exception

    if _sentry_patched_exception:
        return

    def _sentry_patched_record_exception(
        self: "Span", exception: "BaseException", *args: "Any", **kwargs: "Any"
    ) -> None:
        otlp_integration = get_client().get_integration(OTLPIntegration)
        if otlp_integration and otlp_integration.capture_exceptions:
            with capture_internal_exceptions():
                event, hint = event_from_exception(
                    exception,
                    client_options=get_client().options,
                    mechanism={"type": OTLPIntegration.identifier, "handled": False},
                )
                capture_event(event, hint=hint)

        _original_record_exception(self, exception, *args, **kwargs)

    Span.record_exception = _sentry_patched_record_exception  # type: ignore[method-assign]
    _sentry_patched_exception = True


class SentryOTLPPropagator(TextMapPropagator):
    """
    !!! Note regarding baggage:
    We cannot meaningfully populate a new baggage as a head SDK
    when we are using OTLP since we don't have any sort of transaction semantic to
    track state across a group of spans.

    For incoming baggage, we just pass it on as is so that case is correctly handled.
    """

    def extract(
        self,
        carrier: "CarrierT",
        context: "Optional[Context]" = None,
        getter: "Getter[CarrierT]" = default_getter,
    ) -> "Context":
        if context is None:
            context = get_current()

        sentry_trace = getter.get(carrier, SENTRY_TRACE_HEADER_NAME)
        if not sentry_trace:
            return context

        sentrytrace = extract_sentrytrace_data(sentry_trace[0])
        if not sentrytrace:
            return context

        context = set_value(SENTRY_TRACE_KEY, sentrytrace, context)

        trace_id, span_id = sentrytrace["trace_id"], sentrytrace["parent_span_id"]

        span_context = SpanContext(
            trace_id=int(trace_id, 16),  # type: ignore
            span_id=int(span_id, 16),  # type: ignore
            # we simulate a sampled trace on the otel side and leave the sampling to sentry
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            is_remote=True,
        )

        baggage_header = getter.get(carrier, BAGGAGE_HEADER_NAME)

        if baggage_header:
            baggage = Baggage.from_incoming_header(baggage_header[0])
        else:
            # If there's an incoming sentry-trace but no incoming baggage header,
            # for instance in traces coming from older SDKs,
            # baggage will be empty and frozen and won't be populated as head SDK.
            baggage = Baggage(sentry_items={})

        baggage.freeze()
        context = set_value(SENTRY_BAGGAGE_KEY, baggage, context)

        span = NonRecordingSpan(span_context)
        modified_context = trace.set_span_in_context(span, context)
        return modified_context

    def inject(
        self,
        carrier: "CarrierT",
        context: "Optional[Context]" = None,
        setter: "Setter[CarrierT]" = default_setter,
    ) -> None:
        otlp_integration = get_client().get_integration(OTLPIntegration)
        if otlp_integration is None:
            return

        if context is None:
            context = get_current()

        current_span = get_current_span(context)
        current_span_context = current_span.get_span_context()

        if not current_span_context.is_valid:
            return

        sentry_trace = _to_traceparent(current_span_context)
        setter.set(carrier, SENTRY_TRACE_HEADER_NAME, sentry_trace)

        baggage = get_value(SENTRY_BAGGAGE_KEY, context)
        if baggage is not None and isinstance(baggage, Baggage):
            baggage_data = baggage.serialize()
            if baggage_data:
                setter.set(carrier, BAGGAGE_HEADER_NAME, baggage_data)

    @property
    def fields(self) -> "Set[str]":
        return {SENTRY_TRACE_HEADER_NAME, BAGGAGE_HEADER_NAME}


def _to_traceparent(span_context: "SpanContext") -> str:
    """
    Helper method to generate the sentry-trace header.
    """
    span_id = format_span_id(span_context.span_id)
    trace_id = format_trace_id(span_context.trace_id)
    sampled = span_context.trace_flags.sampled

    return f"{trace_id}-{span_id}-{'1' if sampled else '0'}"


class OTLPIntegration(Integration):
    """
    Automatically setup OTLP ingestion from the DSN.

    :param setup_otlp_traces_exporter: Automatically configure an Exporter to send OTLP traces from the DSN, defaults to True.
        Set to False to setup the TracerProvider manually.
    :param collector_url: URL of your own OpenTelemetry collector, defaults to None.
        When set, the exporter will send traces to this URL instead of the Sentry OTLP endpoint derived from the DSN.
    :param setup_propagator: Automatically configure the Sentry Propagator for Distributed Tracing, defaults to True.
        Set to False to configure propagators manually or to disable propagation.
    :param capture_exceptions: Intercept and capture exceptions on the OpenTelemetry Span in Sentry as well, defaults to False.
        Set to True to turn on capturing but be aware that since Sentry captures most exceptions, duplicate exceptions might be dropped by DedupeIntegration in many cases.
    """

    identifier = "otlp"

    def __init__(
        self,
        setup_otlp_traces_exporter: bool = True,
        collector_url: "Optional[str]" = None,
        setup_propagator: bool = True,
        capture_exceptions: bool = False,
    ) -> None:
        self.setup_otlp_traces_exporter = setup_otlp_traces_exporter
        self.collector_url = collector_url
        self.setup_propagator = setup_propagator
        self.capture_exceptions = capture_exceptions

    @staticmethod
    def setup_once() -> None:
        logger.debug("[OTLP] Setting up trace linking for all events")
        register_external_propagation_context(otel_propagation_context)

    def setup_once_with_options(
        self, options: "Optional[Dict[str, Any]]" = None
    ) -> None:
        if self.setup_otlp_traces_exporter:
            logger.debug("[OTLP] Setting up OTLP exporter")
            dsn: "Optional[str]" = options.get("dsn") if options else None
            setup_otlp_traces_exporter(dsn, collector_url=self.collector_url)

        if self.setup_propagator:
            logger.debug("[OTLP] Setting up propagator for distributed tracing")
            # TODO-neel better propagator support, chain with existing ones if possible instead of replacing
            set_global_textmap(SentryOTLPPropagator())

        setup_capture_exceptions()
