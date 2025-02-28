from http.client import HTTPConnection, HTTPSConnection
from socket import SocketIO
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest import mock

import pytest

from sentry_sdk import capture_message, start_span, continue_trace, isolation_scope
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.integrations.stdlib import StdlibIntegration

from tests.conftest import ApproxDict, SortedBaggage, create_mock_http_server

PORT = create_mock_http_server()


@pytest.fixture
def capture_request_headers(monkeypatch):
    """
    HTTPConnection.send is passed a string containing (among other things)
    the headers on the request. Mock it so we can check the headers.
    """

    def inner(do_send=True):
        request_headers = {}
        old_send = HTTPConnection.send

        def patched_send(self, data):
            for line in data.decode("utf-8").split("\r\n")[1:]:
                if line:
                    key, val = line.split(": ")
                    request_headers[key] = val
            if do_send:
                old_send(self, data)

        monkeypatch.setattr(HTTPConnection, "send", patched_send)
        return request_headers

    return inner


def test_crumb_capture(sentry_init, capture_events):
    sentry_init(integrations=[StdlibIntegration()])
    events = capture_events()

    url = "http://localhost:{}/some/random/url".format(PORT)
    urlopen(url)

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


@pytest.mark.parametrize(
    "status_code,level",
    [
        (200, "info"),
        (301, "info"),
        (403, "warning"),
        (405, "warning"),
        (500, "error"),
    ],
)
def test_crumb_capture_client_error(sentry_init, capture_events, status_code, level):
    sentry_init(integrations=[StdlibIntegration()])
    events = capture_events()

    url = f"http://localhost:{PORT}/status/{status_code}"  # noqa:E231
    try:
        urlopen(url)
    except HTTPError:
        pass

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["level"] == level
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: status_code,
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


def test_crumb_capture_hint(sentry_init, capture_events):
    def before_breadcrumb(crumb, hint):
        crumb["data"]["extra"] = "foo"
        return crumb

    sentry_init(integrations=[StdlibIntegration()], before_breadcrumb=before_breadcrumb)
    events = capture_events()

    url = "http://localhost:{}/some/random/url".format(PORT)
    urlopen(url)

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": url,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            "extra": "foo",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


def test_empty_realurl(sentry_init):
    """
    Ensure that after using sentry_sdk.init you can putrequest a
    None url.
    """

    sentry_init(dsn="")
    HTTPConnection("localhost", PORT).putrequest("POST", None)


def test_httplib_misuse(sentry_init, capture_events, request):
    """HTTPConnection.getresponse must be called after every call to
    HTTPConnection.request. However, if somebody does not abide by
    this contract, we still should handle this gracefully and not
    send mixed breadcrumbs.

    Test whether our breadcrumbs are coherent when somebody uses HTTPConnection
    wrongly.
    """

    sentry_init()
    events = capture_events()

    conn = HTTPConnection("localhost", PORT)

    # make sure we release the resource, even if the test fails
    request.addfinalizer(conn.close)

    conn.request("GET", "/200")

    with pytest.raises(Exception):  # noqa: B017
        # This raises an exception, because we didn't call `getresponse` for
        # the previous request yet.
        #
        # This call should not affect our breadcrumb.
        conn.request("POST", "/200")

    response = conn.getresponse()
    assert response._method == "GET"

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "url": "http://localhost:{}/200".format(PORT),
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_STATUS_CODE: 200,
            "reason": "OK",
            SPANDATA.HTTP_FRAGMENT: "",
            SPANDATA.HTTP_QUERY: "",
        }
    )


def test_outgoing_trace_headers(
    sentry_init, capture_envelopes, capture_request_headers
):
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()
    request_headers = capture_request_headers()

    headers = {
        "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
        "baggage": (
            "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
            "sentry-user_id=Am%C3%A9lie, sentry-sample_rand=0.132521102938283, other-vendor-value-2=foo;bar;"
        ),
    }

    with isolation_scope():
        with continue_trace(headers):
            with start_span(name="/interactions/other-dogs/new-dog"):
                conn = HTTPConnection("localhost", PORT)
                conn.request("GET", "/top-chasers")
                conn.getresponse()

    (envelope,) = envelopes
    transaction = envelope.get_transaction_event()
    request_span = transaction["spans"][-1]

    expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
        trace_id=transaction["contexts"]["trace"]["trace_id"],
        parent_span_id=request_span["span_id"],
        sampled=1,
    )
    assert request_headers["sentry-trace"] == expected_sentry_trace

    expected_outgoing_baggage = (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=1.0,"
        "sentry-user_id=Am%C3%A9lie,"
        "sentry-sample_rand=0.132521102938283"
    )

    assert request_headers["baggage"] == SortedBaggage(expected_outgoing_baggage)


def test_outgoing_trace_headers_head_sdk(
    sentry_init, capture_request_headers, capture_envelopes
):
    sentry_init(traces_sample_rate=0.5, release="foo")
    envelopes = capture_envelopes()
    request_headers = capture_request_headers()

    with mock.patch("sentry_sdk.tracing_utils.Random.uniform", return_value=0.25):
        with isolation_scope():
            with continue_trace({}):
                with start_span(name="Head SDK tx") as root_span:
                    conn = HTTPConnection("localhost", PORT)
                    conn.request("GET", "/top-chasers")
                    conn.getresponse()

    (envelope,) = envelopes
    transaction = envelope.get_transaction_event()
    request_span = transaction["spans"][-1]

    expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
        trace_id=transaction["contexts"]["trace"]["trace_id"],
        parent_span_id=request_span["span_id"],
        sampled=1,
    )
    assert request_headers["sentry-trace"] == expected_sentry_trace

    expected_outgoing_baggage = (
        f"sentry-trace_id={root_span.trace_id},"  # noqa: E231
        "sentry-sample_rand=0.250000,"
        "sentry-environment=production,"
        "sentry-release=foo,"
        "sentry-sample_rate=0.5,"
        "sentry-sampled=true,"
        "sentry-transaction=Head%20SDK%20tx"
    )

    assert request_headers["baggage"] == SortedBaggage(expected_outgoing_baggage)


@pytest.mark.parametrize(
    "trace_propagation_targets,host,path,trace_propagated",
    [
        [
            [],
            "example.com",
            "/",
            False,
        ],
        [
            None,
            "example.com",
            "/",
            False,
        ],
        [
            [MATCH_ALL],
            "example.com",
            "/",
            True,
        ],
        [
            ["https://example.com/"],
            "example.com",
            "/",
            True,
        ],
        [
            ["https://example.com/"],
            "example.com",
            "",
            False,
        ],
        [
            ["https://example.com"],
            "example.com",
            "",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "example.net",
            "",
            False,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "good.example.net",
            "",
            True,
        ],
        [
            ["https://example.com", r"https?:\/\/[\w\-]+(\.[\w\-]+)+\.net"],
            "good.example.net",
            "/some/thing",
            True,
        ],
    ],
)
def test_option_trace_propagation_targets(
    sentry_init,
    capture_request_headers,
    trace_propagation_targets,
    host,
    path,
    trace_propagated,
):
    sentry_init(
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
    )

    request_headers = capture_request_headers(do_send=False)

    with start_span(name="foo"):
        HTTPSConnection(host).request("GET", path)
        # don't invoke getresponse to avoid actual network traffic

        if trace_propagated:
            assert "sentry-trace" in request_headers
            assert "baggage" in request_headers
        else:
            assert "sentry-trace" not in request_headers
            assert "baggage" not in request_headers


def test_span_origin(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, debug=True)
    events = capture_events()

    with start_span(name="foo"):
        conn = HTTPConnection("localhost", PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events
    assert event["contexts"]["trace"]["origin"] == "manual"

    assert event["spans"][0]["op"] == "http.client"
    assert event["spans"][0]["origin"] == "auto.http.stdlib.httplib"


def test_http_timeout(monkeypatch, sentry_init, capture_envelopes):
    mock_readinto = mock.Mock(side_effect=TimeoutError)
    monkeypatch.setattr(SocketIO, "readinto", mock_readinto)

    sentry_init(traces_sample_rate=1.0)

    envelopes = capture_envelopes()

    with start_span(op="op", name="name"):
        try:
            conn = HTTPConnection("localhost", PORT)
            conn.request("GET", "/top-chasers")
            conn.getresponse()
        except Exception:
            pass

    items = [
        item
        for envelope in envelopes
        for item in envelope.items
        if item.type == "transaction"
    ]
    assert len(items) == 1

    transaction = items[0].payload.json
    assert len(transaction["spans"]) == 1

    span = transaction["spans"][0]
    assert span["op"] == "http.client"
    assert (
        span["description"] == f"GET http://localhost:{PORT}/top-chasers"  # noqa: E231
    )
