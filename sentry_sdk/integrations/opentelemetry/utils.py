from typing import cast
from datetime import datetime, timezone

from opentelemetry.trace import SpanKind
from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import ReadableSpan
from urllib3.util import parse_url as urlparse

from sentry_sdk import get_client
from sentry_sdk.utils import Dsn

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Tuple


def is_sentry_span(span):
    # type: (ReadableSpan) -> bool
    """
    Break infinite loop:
    HTTP requests to Sentry are caught by OTel and send again to Sentry.
    """
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


def convert_otel_timestamp(time):
    # type: (int) -> datetime
    return datetime.fromtimestamp(time / 1e9, timezone.utc)


def extract_span_data(span):
    # type: (ReadableSpan) -> Tuple[str, str, Optional[int]]
    op = span.name
    description = span.name
    status_code = None

    if span.attributes is None:
        return (op, description, status_code)

    http_method = span.attributes.get(SpanAttributes.HTTP_METHOD)
    http_method = cast("Optional[str]", http_method)
    if http_method:
        return span_data_for_http_method(span)

    db_query = span.attributes.get(SpanAttributes.DB_SYSTEM)
    if db_query:
        return span_data_for_db_query(span)

    rpc_service = span.attributes.get(SpanAttributes.RPC_SERVICE)
    if rpc_service:
        return ("rpc", description, status_code)

    messaging_system = span.attributes.get(SpanAttributes.MESSAGING_SYSTEM)
    if messaging_system:
        return ("message", description, status_code)

    faas_trigger = span.attributes.get(SpanAttributes.FAAS_TRIGGER)
    if faas_trigger:
        return (str(faas_trigger), description, status_code)

    status_code = cast("Optional[int]", status_code)
    return (op, description, status_code)


def span_data_for_http_method(span):
    # type: (ReadableSpan) -> Tuple[str, str, Optional[int]]
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

    status_code = span_attributes.get(SpanAttributes.HTTP_RESPONSE_STATUS_CODE)
    if status_code is None:
        status_code = span_attributes.get(SpanAttributes.HTTP_STATUS_CODE)

    status_code = cast("Optional[int]", status_code)

    return (op, description, status_code)


def span_data_for_db_query(span):
    # type: (ReadableSpan) -> Tuple[str, str, Optional[int]]
    span_attributes = span.attributes or {}

    op = "db"

    statement = span_attributes.get(SpanAttributes.DB_STATEMENT, None)
    statement = cast("Optional[str]", statement)

    description = statement or span.name

    return (op, description, None)
  