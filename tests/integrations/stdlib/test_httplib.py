import platform
import sys

import pytest

try:
    # py3
    from urllib.request import urlopen
except ImportError:
    # py2
    from urllib import urlopen

try:
    # py2
    from httplib import HTTPSConnection
except ImportError:
    # py3
    from http.client import HTTPSConnection

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.stdlib import StdlibIntegration


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])
    events = capture_events()

    url = "https://httpbin.org/status/200"
    response = urlopen(url)
    assert response.getcode() == 200
    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == {
        "url": url,
        "method": "GET",
        "status_code": 200,
        "reason": "OK",
    }


def test_crumb_capture_hint(sentry_init, capture_events):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[StdlibIntegration()], before_breadcrumb=before_breadcrumb)
    events = capture_events()

    url = "https://httpbin.org/status/200"
    response = urlopen(url)
    assert response.getcode() == 200
    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == {
        "url": url,
        "method": "GET",
        "status_code": 200,
        "reason": "OK",
        "extra": "foo",
    }

    if platform.python_implementation() != "PyPy":
        assert sys.getrefcount(response) == 2


def test_httplib_misuse(sentry_init, capture_events):
    """HTTPConnection.getresponse must be called after every call to
    HTTPConnection.request. However, if somebody does not abide by
    this contract, we still should handle this gracefully and not
    send mixed breadcrumbs.

    Test whether our breadcrumbs are coherent when somebody uses HTTPConnection
    wrongly.
    """

    sentry_init()
    events = capture_events()

    conn = HTTPSConnection("httpbin.org", 443)
    conn.request("GET", "/anything/foo")

    with pytest.raises(Exception):
        # This raises an exception, because we didn't call `getresponse` for
        # the previous request yet.
        #
        # This call should not affect our breadcrumb.
        conn.request("POST", "/anything/bar")

    response = conn.getresponse()
    assert response._method == "GET"

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == {
        "url": "https://httpbin.org/anything/foo",
        "method": "GET",
        "status_code": 200,
        "reason": "OK",
    }


def test_outgoing_trace_headers(
    sentry_init, monkeypatch, StringContaining  # noqa: N803
):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    sentry_init(traces_sample_rate=1.0)

    with start_transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
    ) as transaction:

        HTTPSConnection("www.squirrelchasers.com").request("GET", "/top-chasers")

        request_span = transaction._span_recorder.spans[-1]

        expected_sentry_trace = (
            "sentry-trace: {trace_id}-{parent_span_id}-{sampled}".format(
                trace_id=transaction.trace_id,
                parent_span_id=request_span.span_id,
                sampled=1,
            )
        )

        mock_send.assert_called_with(StringContaining(expected_sentry_trace))
