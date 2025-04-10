"""
IMPORTANT: The contents of this file are part of a proof of concept and as such
are experimental and not suitable for production use. They may be changed or
removed at any time without prior notice.
"""

from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.opentelemetry.propagator import SentryPropagator
from sentry_sdk.opentelemetry.sampler import SentrySampler
from sentry_sdk.opentelemetry.span_processor import SentrySpanProcessor
from sentry_sdk.utils import logger

try:
    from opentelemetry import trace
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.trace import TracerProvider, Span, ReadableSpan
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
        _patch_readable_span()
        # _setup_instrumentors()

        logger.debug("[OTel] Finished setting up OpenTelemetry integration")


def _patch_readable_span():
    # type: () -> None
    """
    We need to pass through sentry specific metadata/objects from Span to ReadableSpan
    to work with them consistently in the SpanProcessor.
    """
    old_readable_span = Span._readable_span

    def sentry_patched_readable_span(self):
        # type: (Span) -> ReadableSpan
        readable_span = old_readable_span(self)
        readable_span._sentry_meta = getattr(self, "_sentry_meta", {})  # type: ignore[attr-defined]
        return readable_span

    Span._readable_span = sentry_patched_readable_span  # type: ignore[method-assign]


def _setup_sentry_tracing():
    # type: () -> None
    provider = TracerProvider(sampler=SentrySampler())
    provider.add_span_processor(SentrySpanProcessor())
    trace.set_tracer_provider(provider)

    set_global_textmap(SentryPropagator())


def _setup_instrumentors():
    # type: () -> None
    for instrumentor, kwargs in CONFIGURABLE_INSTRUMENTATIONS.items():
        instrumentor().instrument(**kwargs)
