import re

BAGGAGE_HEADER_NAME = "baggage"
SENTRY_TRACE_HEADER_NAME = "sentry-trace"

# Transaction source
# see https://develop.sentry.dev/sdk/event-payloads/transaction/#transaction-annotations
TRANSACTION_SOURCE_CUSTOM = "custom"
TRANSACTION_SOURCE_URL = "url"
TRANSACTION_SOURCE_ROUTE = "route"
TRANSACTION_SOURCE_VIEW = "view"
TRANSACTION_SOURCE_COMPONENT = "component"
TRANSACTION_SOURCE_TASK = "task"

# These are typically high cardinality and the server hates them
LOW_QUALITY_TRANSACTION_SOURCES = [
    TRANSACTION_SOURCE_URL,
]

SOURCE_FOR_STYLE = {
    "endpoint": TRANSACTION_SOURCE_COMPONENT,
    "function_name": TRANSACTION_SOURCE_COMPONENT,
    "handler_name": TRANSACTION_SOURCE_COMPONENT,
    "method_and_path_pattern": TRANSACTION_SOURCE_ROUTE,
    "path": TRANSACTION_SOURCE_URL,
    "route_name": TRANSACTION_SOURCE_COMPONENT,
    "route_pattern": TRANSACTION_SOURCE_ROUTE,
    "uri_template": TRANSACTION_SOURCE_ROUTE,
    "url": TRANSACTION_SOURCE_ROUTE,
}

SENTRY_TRACE_REGEX = re.compile(
    "^[ \t]*"  # whitespace
    "([0-9a-f]{32})?"  # trace_id
    "-?([0-9a-f]{16})?"  # span_id
    "-?([01])?"  # sampled
    "[ \t]*$"  # whitespace
)
