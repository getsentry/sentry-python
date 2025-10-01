import pytest
from aiohttp import ClientSession

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.aiohttp import AioHttpIntegration


@pytest.mark.asyncio
async def test_aiohttp_query_source_disabled(sentry_init, capture_events, aiohttp_mock):
    """Test that query source is not added when enable_http_query_source is False."""
    aiohttp_mock.get("https://example.com/test", status=200)
    
    sentry_init(
        integrations=[AioHttpIntegration()],
        enable_tracing=True,
        enable_http_query_source=False,
        http_query_source_threshold_ms=0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        async with ClientSession() as session:
            async with session.get("https://example.com/test") as response:
                await response.text()

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


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_http_query_source", [None, True])
async def test_aiohttp_query_source_enabled(sentry_init, capture_events, aiohttp_mock, enable_http_query_source):
    """Test that query source is added when enabled (explicitly or by default)."""
    aiohttp_mock.get("https://example.com/test", status=200)
    
    sentry_options = {
        "integrations": [AioHttpIntegration()],
        "enable_tracing": True,
        "http_query_source_threshold_ms": 0,
    }
    if enable_http_query_source is not None:
        sentry_options["enable_http_query_source"] = enable_http_query_source

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        async with ClientSession() as session:
            async with session.get("https://example.com/test") as response:
                await response.text()

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
            assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.aiohttp.test_http_query_source"
            assert data.get(SPANDATA.CODE_FILEPATH).endswith("tests/integrations/aiohttp/test_http_query_source.py")
            assert data.get(SPANDATA.CODE_FUNCTION) == "test_aiohttp_query_source_enabled"
            break
    else:
        raise AssertionError("No http.client span found")


@pytest.mark.asyncio
async def test_aiohttp_query_source_threshold(sentry_init, capture_events, aiohttp_mock):
    """Test that query source is only added for slow requests based on threshold."""
    aiohttp_mock.get("https://example.com/test", status=200)
    
    sentry_init(
        integrations=[AioHttpIntegration()],
        enable_tracing=True,
        enable_http_query_source=True,
        http_query_source_threshold_ms=1000000,  # Very high threshold
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        async with ClientSession() as session:
            async with session.get("https://example.com/test") as response:
                await response.text()

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
