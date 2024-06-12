from opentelemetry.semconv.trace import SpanAttributes  # type: ignore
from opentelemetry.trace import Span  # type: ignore

from sentry_sdk import get_client, start_transaction
from sentry_sdk.utils import Dsn

def is_sentry_span(span):
    # type: (Span) -> bool
    """
    Break infinite loop:
    HTTP requests to Sentry are caught by OTel and send again to Sentry.
    """
    span_url = span.attributes.get(SpanAttributes.HTTP_URL, None)

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
