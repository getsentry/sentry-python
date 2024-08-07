from opentelemetry.context import create_key


# propagation keys
SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")

# scope management keys
SENTRY_SCOPES_KEY = create_key("sentry_scopes")
SENTRY_FORK_ISOLATION_SCOPE_KEY = create_key("sentry_fork_isolation_scope")

OTEL_SENTRY_CONTEXT = "otel"
SPAN_ORIGIN = "auto.otel"


class SentrySpanAttribute:
    # XXX better name
    # XXX not all of these need separate attributes, we might just use
    # existing otel attrs for some
    DESCRIPTION = "sentry.description"
    OP = "sentry.op"
    ORIGIN = "sentry.origin"
