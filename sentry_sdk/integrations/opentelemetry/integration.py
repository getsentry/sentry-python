"""
IMPORTANT: The contents of this file are part of a proof of concept and as such
are experimental and not suitable for production use. They may be changed or
removed at any time without prior notice.
"""
import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.utils import logger

try:
    from opentelemetry import trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except ImportError:
    raise DidNotEnable("opentelemetry not installed")

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:
    OTLPSpanExporter = None


try:
    from opentelemetry.instrumentation.django import DjangoInstrumentor  # type: ignore[import-not-found]
except ImportError:
    DjangoInstrumentor = None


CONFIGURABLE_INSTRUMENTATIONS = {
    DjangoInstrumentor: {"is_sql_commentor_enabled": True},
}


class OpenTelemetryIntegration(Integration):
    identifier = "opentelemetry"

    def __init__(self, enable_span_processor=True, enable_otlp_exporter=False, enable_propagator=True):
        # type: (bool, bool, bool) -> None
        self.enable_span_processor = enable_span_processor
        self.enable_otlp_exporter = enable_otlp_exporter
        self.enable_propagator = enable_propagator

        if self.enable_otlp_exporter and OTLPSpanExporter is None:
            logger.warning("[Otel] OTLPSpanExporter not installed.")
            self.enable_otlp_exporter = False

        if self.enable_span_processor and self.enable_otlp_exporter:
            logger.warning("[Otel] Disabling span processor because otlp exporter is set.")
            self.enable_span_processor = False

    @staticmethod
    def setup_once():
        # type: () -> None
        logger.warning(
            "[OTel] Initializing highly experimental OpenTelemetry support. "
            "Use at your own risk."
        )

        _setup_sentry_tracing()
        # _setup_instrumentors()

        logger.debug("[OTel] Finished setting up OpenTelemetry integration")


def _setup_sentry_tracing():
    # type: () -> None
    integration = sentry_sdk.get_client().get_integration(OpenTelemetryIntegration)
    if integration is None:
        return

    # TODO provider oblivious
    provider = TracerProvider()

    if integration.enable_otlp_exporter:
        otlp_exporter = OTLPSpanExporter(
            # endpoint="___OTLP_TRACES_URL___",
            # headers={"x-sentry-auth": "sentry sentry_key=___PUBLIC_KEY___"},
        )
        otlp_span_processor = BatchSpanProcessor(otlp_exporter)
        provider.add_span_processor(otlp_span_processor)
    elif integration.enable_span_processor:
        provider.add_span_processor(SentrySpanProcessor())
    trace.set_tracer_provider(provider)

    if integration.enable_propagator:
        set_global_textmap(SentryPropagator())


def _setup_instrumentors():
    # type: () -> None
    for instrumentor, kwargs in CONFIGURABLE_INSTRUMENTATIONS.items():
        instrumentor().instrument(**kwargs)
