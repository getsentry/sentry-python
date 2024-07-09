from opentelemetry.context import create_key


SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")
OTEL_SENTRY_CONTEXT = "otel"
SPAN_ORIGIN = "auto.otel"
