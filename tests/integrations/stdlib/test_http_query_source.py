import pytest
from http.client import HTTPConnection

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration

from tests.conftest import create_mock_http_server

PORT = create_mock_http_server()


def test_http_query_source_disabled(sentry_init, capture_events):
    """Test that query source is not added when enable_http_query_source is False."""
    sentry_init(
        integrations=[StdlibIntegration()],
        enable_tracing=True,
        enable_http_query_source=False,
        http_query_source_threshold_ms=0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/test")
        conn.getresponse()

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
def test_http_query_source_enabled(sentry_init, capture_events, enable_http_query_source):
    """Test that query source is added when enabled (explicitly or by default)."""
    sentry_options = {
        "integrations": [StdlibIntegration()],
        "enable_tracing": True,
        "http_query_source_threshold_ms": 0,
    }
    if enable_http_query_source is not None:
        sentry_options["enable_http_query_source"] = enable_http_query_source

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/test")
        conn.getresponse()

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
            assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.stdlib.test_http_query_source"
            assert data.get(SPANDATA.CODE_FILEPATH).endswith("tests/integrations/stdlib/test_http_query_source.py")
            assert data.get(SPANDATA.CODE_FUNCTION) == "test_http_query_source_enabled"
            break
    else:
        raise AssertionError("No http.client span found")


def test_http_query_source_threshold(sentry_init, capture_events):
    """Test that query source is only added for slow requests based on threshold."""
    sentry_init(
        integrations=[StdlibIntegration()],
        enable_tracing=True,
        enable_http_query_source=True,
        http_query_source_threshold_ms=1000000,  # Very high threshold
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/test")
        conn.getresponse()

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


def test_http_query_source_with_threshold_met(sentry_init, capture_events, monkeypatch):
    """Test that query source is added when request duration exceeds threshold."""
    import time
    from sentry_sdk.integrations import stdlib
    
    # Mock the getresponse to simulate a slow request
    original_getresponse = HTTPConnection.getresponse
    
    def slow_getresponse(self, *args, **kwargs):
        time.sleep(0.1)  # Simulate 100ms response time
        return original_getresponse(self, *args, **kwargs)
    
    monkeypatch.setattr(HTTPConnection, "getresponse", slow_getresponse)
    
    sentry_init(
        integrations=[StdlibIntegration()],
        enable_tracing=True,
        enable_http_query_source=True,
        http_query_source_threshold_ms=50,  # 50ms threshold
    )
    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/test")
        conn.getresponse()

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "http.client":
            data = span.get("data", {})
            
            # Query source should be added because request is slow
            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data
            
            assert data.get(SPANDATA.CODE_FUNCTION) == "test_http_query_source_with_threshold_met"
            break
    else:
        raise AssertionError("No http.client span found")
