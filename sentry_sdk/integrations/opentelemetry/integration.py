"""
IMPORTANT: The contents of this file are part of a proof of concept and as such
are experimental and not suitable for production use. They may be changed or
removed at any time without prior notice.
"""

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.integrations.opentelemetry.potel_span_processor import (
    PotelSentrySpanProcessor,
)
from sentry_sdk.integrations.opentelemetry.contextvars_context import (
    SentryContextVarsRuntimeContext,
)
from sentry_sdk.integrations.opentelemetry.sampler import SentrySampler
from sentry_sdk.utils import logger

try:
    from opentelemetry import trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider
except ImportError:
    raise DidNotEnable("opentelemetry not installed")

try:
    from opentelemetry.instrumentation.django import DjangoInstrumentor  # type: ignore[import-not-found]
except ImportError:
    DjangoInstrumentor = None


CONFIGURABLE_INSTRUMENTATIONS = {
    DjangoInstrumentor: {"is_sql_commentor_enabled": True},
}


class OpenTelemetryIntegration(Integration):
    identifier = "opentelemetry"

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
    import opentelemetry.context

    opentelemetry.context._RUNTIME_CONTEXT = SentryContextVarsRuntimeContext()

    provider = TracerProvider(sampler=SentrySampler())
    provider.add_span_processor(PotelSentrySpanProcessor())
    trace.set_tracer_provider(provider)

    set_global_textmap(SentryPropagator())


def _setup_instrumentors():
    # type: () -> None
    for instrumentor, kwargs in CONFIGURABLE_INSTRUMENTATIONS.items():
        instrumentor().instrument(**kwargs)
