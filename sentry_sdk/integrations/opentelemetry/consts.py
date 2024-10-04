from opentelemetry.context import create_key
from sentry_sdk.tracing_utils import Baggage


# propagation keys
SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")

# scope management keys
SENTRY_SCOPES_KEY = create_key("sentry_scopes")
SENTRY_FORK_ISOLATION_SCOPE_KEY = create_key("sentry_fork_isolation_scope")
SENTRY_USE_CURRENT_SCOPE_KEY = create_key("sentry_use_current_scope")
SENTRY_USE_ISOLATION_SCOPE_KEY = create_key("sentry_use_isolation_scope")

TRACESTATE_SAMPLED_KEY = Baggage.SENTRY_PREFIX + "sampled"
TRACESTATE_SAMPLE_RATE_KEY = Baggage.SENTRY_PREFIX + "sample_rate"

OTEL_SENTRY_CONTEXT = "otel"
SPAN_ORIGIN = "auto.otel"


class SentrySpanAttribute:
    # XXX not all of these need separate attributes, we might just use
    # existing otel attrs for some
    DESCRIPTION = "sentry.description"
    OP = "sentry.op"
    ORIGIN = "sentry.origin"
    MEASUREMENT = "sentry.measurement"
    TAG = "sentry.tag"
    NAME = "sentry.name"
