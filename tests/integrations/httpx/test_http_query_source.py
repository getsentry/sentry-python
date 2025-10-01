import pytest
import httpx

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration


def test_httpx_query_source_disabled(sentry_init, capture_events, httpx_mock):
    """Test that query source is not added when enable_http_query_source is False."""
    httpx_mock.add_response()
    
    sentry_init(
        integrations=[HttpxIntegration()],
        enable_tracing=True,
        enable_http_query_source=False,
        http_query_source_threshold_ms=0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        with httpx.Client() as client:
            client.get("https://example.com/test")

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "http.client":
            data = span.get("data", {})
            
            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data
            break
    else:
        raise AssertionError("No http.client span found")


@pytest.mark.parametrize("enable_http_query_source", [None, True])
def test_httpx_query_source_enabled(sentry_init, capture_events, httpx_mock, enable_http_query_source):
    """Test that query source is added when enabled (explicitly or by default)."""
    httpx_mock.add_response()
    
    sentry_options = {
        "integrations": [HttpxIntegration()],
        "enable_tracing": True,
        "http_query_source_threshold_ms": 0,
    }
    if enable_http_query_source is not None:
        sentry_options["enable_http_query_source"] = enable_http_query_source

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        with httpx.Client() as client:
            client.get("https://example.com/test")

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "http.client":
            data = span.get("data", {})
            
            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data
            
            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0
            assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.httpx.test_http_query_source"
            assert data.get(SPANDATA.CODE_FILEPATH).endswith("tests/integrations/httpx/test_http_query_source.py")
            assert data.get(SPANDATA.CODE_FUNCTION) == "test_httpx_query_source_enabled"
            break
    else:
        raise AssertionError("No http.client span found")


def test_httpx_query_source_threshold(sentry_init, capture_events, httpx_mock):
    """Test that query source is only added for slow requests based on threshold."""
    httpx_mock.add_response()
    
    sentry_init(
        integrations=[HttpxIntegration()],
        enable_tracing=True,
        enable_http_query_source=True,
        http_query_source_threshold_ms=1000000,  # Very high threshold
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        with httpx.Client() as client:
            client.get("https://example.com/test")

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "http.client":
            data = span.get("data", {})
            
            # Query source should not be added because request is too fast
            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data
            break
    else:
        raise AssertionError("No http.client span found")


@pytest.mark.asyncio
async def test_httpx_async_query_source_enabled(sentry_init, capture_events, httpx_mock):
    """Test that query source works with async httpx client."""
    httpx_mock.add_response()
    
    sentry_init(
        integrations=[HttpxIntegration()],
        enable_tracing=True,
        enable_http_query_source=True,
        http_query_source_threshold_ms=0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        async with httpx.AsyncClient() as client:
            await client.get("https://example.com/test")

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "http.client":
            data = span.get("data", {})
            
            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data
            
            assert data.get(SPANDATA.CODE_FUNCTION) == "test_httpx_async_query_source_enabled"
            break
    else:
        raise AssertionError("No http.client span found")
