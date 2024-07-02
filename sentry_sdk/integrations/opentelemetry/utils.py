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
    db_query = span.attributes.get(SpanAttributes.DB_SYSTEM)

    if http_method:
        op = "http"
        if span.kind == SpanKind.SERVER:
            op += ".server"
        elif span.kind == SpanKind.CLIENT:
            op += ".client"

        description = http_method

        peer_name = span.attributes.get(SpanAttributes.NET_PEER_NAME, None)
        if peer_name:
            description += " {}".format(peer_name)

        target = span.attributes.get(SpanAttributes.HTTP_TARGET, None)
        if target:
            description += " {}".format(target)

        if not peer_name and not target:
            url = span.attributes.get(SpanAttributes.HTTP_URL, None)
            url = cast("Optional[str]", url)
            if url:
                parsed_url = urlparse(url)
                url = "{}://{}{}".format(
                    parsed_url.scheme, parsed_url.netloc, parsed_url.path
                )
                description += " {}".format(url)

        status_code = span.attributes.get(SpanAttributes.HTTP_STATUS_CODE)
    elif db_query:
        op = "db"
        statement = span.attributes.get(SpanAttributes.DB_STATEMENT, None)
        statement = cast("Optional[str]", statement)
        if statement:
            description = statement

    status_code = cast("Optional[int]", status_code)
    return (op, description, status_code)
