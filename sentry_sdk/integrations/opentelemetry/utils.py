from typing import cast
from datetime import datetime, timezone

from opentelemetry.semconv.trace import SpanAttributes
from opentelemetry.sdk.trace import ReadableSpan

from sentry_sdk import get_client
from sentry_sdk.utils import Dsn

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional


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
