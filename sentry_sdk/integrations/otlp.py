from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.scope import register_external_propagation_context
from sentry_sdk.utils import logger, Dsn
from sentry_sdk.consts import VERSION, EndpointType

try:
    from opentelemetry import trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
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
    ctx = trace.get_current_span().get_span_context()

    if ctx.trace_id == trace.INVALID_TRACE_ID or ctx.span_id == trace.INVALID_SPAN_ID:
        return None

    return (trace.format_trace_id(ctx.trace_id), trace.format_span_id(ctx.span_id))


def setup_otlp_traces_exporter(dsn=None):
    # type: (Optional[str]) -> None
    tracer_provider = trace.get_tracer_provider()

    if not isinstance(tracer_provider, TracerProvider):
        logger.debug("[OTLP] No TracerProvider configured by user, creating a new one")
        tracer_provider = TracerProvider()
        trace.set_tracer_provider(tracer_provider)

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
            set_global_textmap(SentryPropagator())
