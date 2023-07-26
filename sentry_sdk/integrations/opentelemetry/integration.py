from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.utils import logger

try:
    from opentelemetry import trace
    from opentelemetry.instrumentation.auto_instrumentation._load import (
        _load_distro,
        _load_instrumentors,
    )
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
except ImportError:
    raise DidNotEnable("opentelemetry not installed")


class OpenTelemetryIntegration(Integration):
    identifier = "opentelemetry"

    # XXX: otel config

    @staticmethod
    def setup_once():
        # type: () -> None
        try:
            distro = _load_distro()
            distro.configure()
            _load_instrumentors(distro)
        except Exception:
            logger.exception("Failed to auto initialize opentelemetry")

        _setup_sentry_tracing()

        logger.debug("Finished setting up OTel integration")


def _setup_sentry_tracing():
    # type: () -> None

    provider = TracerProvider()

    # XXX for debugging
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    # XXX end

    provider.add_span_processor(SentrySpanProcessor())

    trace.set_tracer_provider(provider)

    set_global_textmap(SentryPropagator())
