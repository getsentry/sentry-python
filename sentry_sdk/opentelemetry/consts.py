from opentelemetry.context import create_key


# propagation keys
SENTRY_TRACE_KEY = create_key("sentry-trace")
SENTRY_BAGGAGE_KEY = create_key("sentry-baggage")

# scope management keys
SENTRY_SCOPES_KEY = create_key("sentry_scopes")
SENTRY_FORK_ISOLATION_SCOPE_KEY = create_key("sentry_fork_isolation_scope")
SENTRY_USE_CURRENT_SCOPE_KEY = create_key("sentry_use_current_scope")
SENTRY_USE_ISOLATION_SCOPE_KEY = create_key("sentry_use_isolation_scope")

# trace state keys
SENTRY_PREFIX = "sentry-"
TRACESTATE_SAMPLED_KEY = SENTRY_PREFIX + "sampled"
TRACESTATE_SAMPLE_RATE_KEY = SENTRY_PREFIX + "sample_rate"
TRACESTATE_SAMPLE_RAND_KEY = SENTRY_PREFIX + "sample_rand"

# misc
OTEL_SENTRY_CONTEXT = "otel"
SPAN_ORIGIN = "auto.otel"

# resource semconv attributes
# Not all of these are stable yet, so defining them here rather than importing.
# https://github.com/open-telemetry/semantic-conventions/blob/main/docs/resource/README.md#service
RESOURCE_SERVICE_NAME = "service.name"
RESOURCE_SERVICE_NAMESPACE = "service.namespace"
RESOURCE_SERVICE_VERSION = "service.version"


class SentrySpanAttribute:
    DESCRIPTION = "sentry.description"
    OP = "sentry.op"
    ORIGIN = "sentry.origin"
    TAG = "sentry.tag"
    NAME = "sentry.name"
    SOURCE = "sentry.source"
    CONTEXT = "sentry.context"
    CUSTOM_SAMPLED = "sentry.custom_sampled"  # used for saving start_span(sampled=X)
