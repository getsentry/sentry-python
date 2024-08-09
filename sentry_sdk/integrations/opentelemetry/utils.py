from typing import cast
from datetime import datetime, timezone

from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import ReadableSpan
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.tracing import get_span_status_from_http_code
from sentry_sdk.integrations.opentelemetry.consts import SentrySpanAttribute
from urllib3.util import parse_url as urlparse

from sentry_sdk.utils import Dsn

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Mapping, Sequence


GRPC_ERROR_MAP = {
    "1": SPANSTATUS.CANCELLED,
    "2": SPANSTATUS.UNKNOWN_ERROR,
    "3": SPANSTATUS.INVALID_ARGUMENT,
    "4": SPANSTATUS.DEADLINE_EXCEEDED,
    "5": SPANSTATUS.NOT_FOUND,
    "6": SPANSTATUS.ALREADY_EXISTS,
    "7": SPANSTATUS.PERMISSION_DENIED,
    "8": SPANSTATUS.RESOURCE_EXHAUSTED,
    "9": SPANSTATUS.FAILED_PRECONDITION,
    "10": SPANSTATUS.ABORTED,
    "11": SPANSTATUS.OUT_OF_RANGE,
    "12": SPANSTATUS.UNIMPLEMENTED,
    "13": SPANSTATUS.INTERNAL_ERROR,
    "14": SPANSTATUS.UNAVAILABLE,
    "15": SPANSTATUS.DATA_LOSS,
    "16": SPANSTATUS.UNAUTHENTICATED,
}


def is_sentry_span(span):
    # type: (ReadableSpan) -> bool
    """
    Break infinite loop:
    HTTP requests to Sentry are caught by OTel and send again to Sentry.
    """
    from sentry_sdk import get_client

    if not span.attributes:
        return False

    span_url = span.attributes.get(SpanAttributes.HTTP_URL, None)
    span_url = cast("Optional[str]", span_url)

    if not span_url:
        return False

    dsn_url = None
    client = get_client()

    if client.dsn:
        try:
            dsn_url = Dsn(client.dsn).netloc
        except Exception:
            pass

    if not dsn_url:
        return False

    if dsn_url in span_url:
        return True

    return False


def convert_from_otel_timestamp(time):
    # type: (int) -> datetime
    """Convert an OTel ns-level timestamp to a datetime."""
    return datetime.fromtimestamp(time / 1e9, timezone.utc)


def convert_to_otel_timestamp(time):
    # type: (Union[datetime.datetime, float]) -> int
    """Convert a datetime to an OTel timestamp (with ns precision)."""
    if isinstance(time, datetime):
        return int(time.timestamp() * 1e9)
    return int(time * 1e9)


def extract_span_data(span):
    # type: (ReadableSpan) -> tuple[str, str, Optional[str], Optional[int], Optional[str]]
    op = span.name
    description = span.name
    status, http_status = extract_span_status(span)
    origin = None

    if span.attributes is None:
        return (op, description, status, http_status, origin)

    origin = span.attributes.get(SentrySpanAttribute.ORIGIN)
    description = span.attributes.get(SentrySpanAttribute.DESCRIPTION) or description

    http_method = span.attributes.get(SpanAttributes.HTTP_METHOD)
    http_method = cast("Optional[str]", http_method)
    if http_method:
        return span_data_for_http_method(span)

    db_query = span.attributes.get(SpanAttributes.DB_SYSTEM)
    if db_query:
        return span_data_for_db_query(span)

    rpc_service = span.attributes.get(SpanAttributes.RPC_SERVICE)
    if rpc_service:
        return ("rpc", description, status, http_status, origin)

    messaging_system = span.attributes.get(SpanAttributes.MESSAGING_SYSTEM)
    if messaging_system:
        return ("message", description, status, http_status, origin)

    faas_trigger = span.attributes.get(SpanAttributes.FAAS_TRIGGER)
    if faas_trigger:
        return (str(faas_trigger), description, status, http_status, origin)

    return (op, description, status, http_status, origin)


def span_data_for_http_method(span):
    # type: (ReadableSpan) -> tuple[str, str, Optional[str], Optional[int], Optional[str]]
    span_attributes = span.attributes or {}

    op = "http"

    if span.kind == SpanKind.SERVER:
        op += ".server"
    elif span.kind == SpanKind.CLIENT:
        op += ".client"

    http_method = span_attributes.get(SpanAttributes.HTTP_METHOD)
    route = span_attributes.get(SpanAttributes.HTTP_ROUTE)
    target = span_attributes.get(SpanAttributes.HTTP_TARGET)
    peer_name = span_attributes.get(SpanAttributes.NET_PEER_NAME)

    description = f"{http_method}"

    if route:
        description = f"{http_method} {route}"
    elif target:
        description = f"{http_method} {target}"
    elif peer_name:
        description = f"{http_method} {peer_name}"
    else:
        url = span_attributes.get(SpanAttributes.HTTP_URL)
        url = cast("Optional[str]", url)

        if url:
            parsed_url = urlparse(url)
            url = "{}://{}{}".format(
                parsed_url.scheme, parsed_url.netloc, parsed_url.path
            )
            description = f"{http_method} {url}"

    status, http_status = extract_span_status(span)

    origin = span_attributes.get(SentrySpanAttribute.ORIGIN)

    return (op, description, status, http_status, origin)


def span_data_for_db_query(span):
    # type: (ReadableSpan) -> tuple[str, str, Optional[str], Optional[int], Optional[str]]
    span_attributes = span.attributes or {}

    op = "db"

    statement = span_attributes.get(SpanAttributes.DB_STATEMENT, None)
    statement = cast("Optional[str]", statement)

    description = statement or span.name
    origin = span_attributes.get(SentrySpanAttribute.ORIGIN)

    return (op, description, None, None, origin)


def extract_span_status(span):
    # type: (ReadableSpan) -> tuple[Optional[str], Optional[int]]
    span_attributes = span.attributes or {}
    status = span.status or None

    if status:
        inferred_status, http_status = infer_status_from_attributes(span_attributes)

        if status.status_code == StatusCode.OK:
            return (SPANSTATUS.OK, http_status)
        elif status.status_code == StatusCode.ERROR:
            if status.description is None:
                if inferred_status:
                    return (inferred_status, http_status)

            if (
                status.description is not None
                and status.description in GRPC_ERROR_MAP.values()
            ):
                return (status.description, None)
            else:
                return (SPANSTATUS.UNKNOWN_ERROR, None)

    inferred_status, http_status = infer_status_from_attributes(span_attributes)
    if inferred_status:
        return (inferred_status, http_status)

    if status and status.status_code == StatusCode.UNSET:
        return (SPANSTATUS.OK, None)
    else:
        return (SPANSTATUS.UNKNOWN_ERROR, None)


def infer_status_from_attributes(span_attributes):
    # type: (Mapping[str, str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]]) -> tuple[Optional[str], Optional[int]]
    http_status = get_http_status_code(span_attributes)

    if http_status:
        return (get_span_status_from_http_code(http_status), http_status)

    grpc_status = span_attributes.get(SpanAttributes.RPC_GRPC_STATUS_CODE)
    if grpc_status:
        return (GRPC_ERROR_MAP.get(str(grpc_status), SPANSTATUS.UNKNOWN_ERROR), None)

    return (None, None)


def get_http_status_code(span_attributes):
    # type: (Mapping[str, str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]]) -> Optional[int]
    http_status = span_attributes.get(SpanAttributes.HTTP_RESPONSE_STATUS_CODE)

    if http_status is None:
        # Fall back to the deprecated attribute
        http_status = span_attributes.get(SpanAttributes.HTTP_STATUS_CODE)

    http_status = cast("Optional[int]", http_status)

    return http_status
