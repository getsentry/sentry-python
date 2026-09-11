from enum import Enum
from typing import TYPE_CHECKING

from sentry_sdk.consts import SPANSTATUS

if TYPE_CHECKING:
    from typing import (
        ParamSpec,
        TypeVar,
    )

    from typing_extensions import TypedDict

    P = ParamSpec("P")
    R = TypeVar("R")

    ProfileContext = TypedDict(
        "ProfileContext",
        {
            "profiler_id": str,
        },
    )

BAGGAGE_HEADER_NAME = "baggage"
SENTRY_TRACE_HEADER_NAME = "sentry-trace"


# Transaction source
# see https://develop.sentry.dev/sdk/event-payloads/transaction/#transaction-annotations
class TransactionSource(str, Enum):
    COMPONENT = "component"
    CUSTOM = "custom"
    ROUTE = "route"
    TASK = "task"
    URL = "url"
    VIEW = "view"

    def __str__(self) -> str:
        return self.value


# These are typically high cardinality and the server hates them
LOW_QUALITY_TRANSACTION_SOURCES = [
    TransactionSource.URL,
]

SOURCE_FOR_STYLE = {
    "endpoint": TransactionSource.COMPONENT,
    "function_name": TransactionSource.COMPONENT,
    "handler_name": TransactionSource.COMPONENT,
    "method_and_path_pattern": TransactionSource.ROUTE,
    "path": TransactionSource.URL,
    "route_name": TransactionSource.COMPONENT,
    "route_pattern": TransactionSource.ROUTE,
    "uri_template": TransactionSource.ROUTE,
    "url": TransactionSource.ROUTE,
}


def get_span_status_from_http_code(http_status_code: int) -> str:
    """
    Returns the Sentry status corresponding to the given HTTP status code.

    See: https://develop.sentry.dev/sdk/event-payloads/contexts/#trace-context
    """
    if http_status_code < 400:
        return SPANSTATUS.OK

    elif 400 <= http_status_code < 500:
        if http_status_code == 403:
            return SPANSTATUS.PERMISSION_DENIED
        elif http_status_code == 404:
            return SPANSTATUS.NOT_FOUND
        elif http_status_code == 429:
            return SPANSTATUS.RESOURCE_EXHAUSTED
        elif http_status_code == 413:
            return SPANSTATUS.FAILED_PRECONDITION
        elif http_status_code == 401:
            return SPANSTATUS.UNAUTHENTICATED
        elif http_status_code == 409:
            return SPANSTATUS.ALREADY_EXISTS
        else:
            return SPANSTATUS.INVALID_ARGUMENT

    elif 500 <= http_status_code < 600:
        if http_status_code == 504:
            return SPANSTATUS.DEADLINE_EXCEEDED
        elif http_status_code == 501:
            return SPANSTATUS.UNIMPLEMENTED
        elif http_status_code == 503:
            return SPANSTATUS.UNAVAILABLE
        else:
            return SPANSTATUS.INTERNAL_ERROR

    return SPANSTATUS.UNKNOWN_ERROR


# Circular imports
