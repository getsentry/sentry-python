from opentelemetry import trace
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.trace import TracerProvider, Span, ReadableSpan

from sentry_sdk.opentelemetry import (
    SentryPropagator,
    SentrySampler,
    SentrySpanProcessor,
)
from sentry_sdk.utils import logger


def patch_readable_span():
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


def setup_sentry_tracing():
    # type: () -> None

    # TracerProvider can only be set once. If we're the first ones setting it,
    # there's no issue. If it already exists, we need to patch it.
    from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE

    if _TRACER_PROVIDER_SET_ONCE._done:
        logger.debug("[Tracing] Detected an existing TracerProvider, patching")
        tracer_provider = trace.get_tracer_provider()
        tracer_provider.add_span_processor(SentrySpanProcessor())
        tracer_provider.sampler = SentrySampler()

    else:
        logger.debug("[Tracing] No TracerProvider set, creating a new one")
        tracer_provider = TracerProvider(sampler=SentrySampler())
        tracer_provider.add_span_processor(SentrySpanProcessor())
        trace.set_tracer_provider(tracer_provider)

    set_global_textmap(SentryPropagator())
