from sentry_sdk import get_client
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.scope import register_external_propagation_context
from sentry_sdk.utils import logger, Dsn
from sentry_sdk.consts import VERSION, EndpointType
from sentry_sdk.tracing_utils import Baggage
from sentry_sdk.tracing import (
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
)

try:
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from opentelemetry.trace import (
        get_current_span,
        get_tracer_provider,
        set_tracer_provider,
        format_trace_id,
        format_span_id,
        SpanContext,
        INVALID_SPAN_ID,
        INVALID_TRACE_ID,
    )

    from opentelemetry.context import (
        Context,
        get_current,
        get_value,
    )

    from opentelemetry.propagators.textmap import (
        CarrierT,
        Setter,
        default_setter,
    )

    from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
    from sentry_sdk.integrations.opentelemetry.consts import SENTRY_BAGGAGE_KEY
except ImportError:
    raise DidNotEnable("opentelemetry-distro[otlp] is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Dict, Any, Tuple


def otel_propagation_context():
    # type: () -> Optional[Tuple[str, str]]
    """
    Get the (trace_id, span_id) from opentelemetry if exists.
    """
    ctx = get_current_span().get_span_context()

    if ctx.trace_id == INVALID_TRACE_ID or ctx.span_id == INVALID_SPAN_ID:
        return None

    return (format_trace_id(ctx.trace_id), format_span_id(ctx.span_id))


def setup_otlp_traces_exporter(dsn=None):
    # type: (Optional[str]) -> None
    tracer_provider = get_tracer_provider()

    if not isinstance(tracer_provider, TracerProvider):
        logger.debug("[OTLP] No TracerProvider configured by user, creating a new one")
        tracer_provider = TracerProvider()
        set_tracer_provider(tracer_provider)

    endpoint = None
    headers = None
    if dsn:
        auth = Dsn(dsn).to_auth(f"sentry.python/{VERSION}")
        endpoint = auth.get_api_url(EndpointType.OTLP_TRACES)
        headers = {"X-Sentry-Auth": auth.to_header()}
        logger.debug(f"[OTLP] Sending traces to {endpoint}")

    otlp_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
    span_processor = BatchSpanProcessor(otlp_exporter)
    tracer_provider.add_span_processor(span_processor)


class SentryOTLPPropagator(SentryPropagator):
    """
    We need to override the inject of the older propagator since that
    is SpanProcessor based.

    !!! Note regarding baggage:
    We cannot meaningfully populate a new baggage as a head SDK
    when we are using OTLP since we don't have any sort of transaction semantic to
    track state across a group of spans.

    For incoming baggage, we just pass it on as is so that case is correctly handled.
    """

    def inject(self, carrier, context=None, setter=default_setter):
        # type: (CarrierT, Optional[Context], Setter[CarrierT]) -> None
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


def _to_traceparent(span_context):
    # type: (SpanContext) -> str
    """
    Helper method to generate the sentry-trace header.
    """
    span_id = format_span_id(span_context.span_id)
    trace_id = format_trace_id(span_context.trace_id)
    sampled = span_context.trace_flags.sampled

    return f"{trace_id}-{span_id}-{'1' if sampled else '0'}"


class OTLPIntegration(Integration):
    identifier = "otlp"

    def __init__(self, setup_otlp_traces_exporter=True, setup_propagator=True):
        # type: (bool, bool) -> None
        self.setup_otlp_traces_exporter = setup_otlp_traces_exporter
        self.setup_propagator = setup_propagator

    @staticmethod
    def setup_once():
        # type: () -> None
        logger.debug("[OTLP] Setting up trace linking for all events")
        register_external_propagation_context(otel_propagation_context)

    def setup_once_with_options(self, options=None):
        # type: (Optional[Dict[str, Any]]) -> None
        if self.setup_otlp_traces_exporter:
            logger.debug("[OTLP] Setting up OTLP exporter")
            dsn = options.get("dsn") if options else None  # type: Optional[str]
            setup_otlp_traces_exporter(dsn)

        if self.setup_propagator:
            logger.debug("[OTLP] Setting up propagator for distributed tracing")
            # TODO-neel better propagator support, chain with existing ones if possible instead of replacing
            set_global_textmap(SentryOTLPPropagator())
