from opentelemetry import trace

import sentry_sdk
from sentry_sdk.tracing import Span


tracer = trace.get_tracer(__name__)


def test_scope_span_reference_started_with_sentry(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with sentry_sdk.start_span(name="test") as span:
        assert sentry_sdk.get_current_span() == span
        assert sentry_sdk.get_current_scope().span == span


def test_scope_span_reference_started_with_otel(sentry_init):
    sentry_init(traces_sample_rate=1.0)

    with tracer.start_as_current_span("test") as otel_span:
        wrapped_span = Span(otel_span=otel_span)
        assert sentry_sdk.get_current_span() == wrapped_span
        assert sentry_sdk.get_current_scope().span == wrapped_span
