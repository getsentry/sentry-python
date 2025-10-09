import os
import datetime
from http.client import HTTPConnection, HTTPSConnection
from socket import SocketIO
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest import mock

import pytest

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.tracing import Transaction
from sentry_sdk.integrations.stdlib import StdlibIntegration

from tests.conftest import ApproxDict, create_mock_http_server

PORT = create_mock_http_server()


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
        (200, None),
        (301, None),
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

    if level is None:
        assert "level" not in crumb
    else:
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
    HTTPConnection("localhost", port=PORT).putrequest("POST", None)


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


def test_outgoing_trace_headers(sentry_init, monkeypatch):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    sentry_init(traces_sample_rate=1.0)

    headers = {
        "baggage": (
            "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
            "sentry-user_id=Am%C3%A9lie, sentry-sample_rand=0.132521102938283, other-vendor-value-2=foo;bar;"
        ),
    }

    transaction = Transaction.continue_from_headers(headers)

    with start_transaction(
        transaction=transaction,
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
    ) as transaction:
        HTTPSConnection("www.squirrelchasers.com").request("GET", "/top-chasers")

        (request_str,) = mock_send.call_args[0]
        request_headers = {}
        for line in request_str.decode("utf-8").split("\r\n")[1:]:
            if line:
                key, val = line.split(": ")
                request_headers[key] = val

        request_span = transaction._span_recorder.spans[-1]
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
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

        assert request_headers["baggage"] == expected_outgoing_baggage


def test_outgoing_trace_headers_head_sdk(sentry_init, monkeypatch):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    sentry_init(traces_sample_rate=0.5, release="foo")
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=250000):
        transaction = Transaction.continue_from_headers({})

    with start_transaction(transaction=transaction, name="Head SDK tx") as transaction:
        HTTPSConnection("www.squirrelchasers.com").request("GET", "/top-chasers")

        (request_str,) = mock_send.call_args[0]
        request_headers = {}
        for line in request_str.decode("utf-8").split("\r\n")[1:]:
            if line:
                key, val = line.split(": ")
                request_headers[key] = val

        request_span = transaction._span_recorder.spans[-1]
        expected_sentry_trace = "{trace_id}-{parent_span_id}-{sampled}".format(
            trace_id=transaction.trace_id,
            parent_span_id=request_span.span_id,
            sampled=1,
        )
        assert request_headers["sentry-trace"] == expected_sentry_trace

        expected_outgoing_baggage = (
            "sentry-trace_id=%s,"
            "sentry-sample_rand=0.250000,"
            "sentry-environment=production,"
            "sentry-release=foo,"
            "sentry-sample_rate=0.5,"
            "sentry-sampled=%s"
        ) % (transaction.trace_id, "true" if transaction.sampled else "false")

        assert request_headers["baggage"] == expected_outgoing_baggage


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
    sentry_init, monkeypatch, trace_propagation_targets, host, path, trace_propagated
):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    sentry_init(
        trace_propagation_targets=trace_propagation_targets,
        traces_sample_rate=1.0,
    )

    headers = {
        "baggage": (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, "
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
        )
    }

    transaction = Transaction.continue_from_headers(headers)

    with start_transaction(
        transaction=transaction,
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
    ) as transaction:
        HTTPSConnection(host).request("GET", path)

        (request_str,) = mock_send.call_args[0]
        request_headers = {}
        for line in request_str.decode("utf-8").split("\r\n")[1:]:
            if line:
                key, val = line.split(": ")
                request_headers[key] = val

        if trace_propagated:
            assert "sentry-trace" in request_headers
            assert "baggage" in request_headers
        else:
            assert "sentry-trace" not in request_headers
            assert "baggage" not in request_headers


def test_request_source_disabled(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=False,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.parametrize("enable_http_request_source", [None, True])
def test_request_source_enabled(
    sentry_init, capture_events, enable_http_request_source
):
    sentry_options = {
        "traces_sample_rate": 1.0,
        "http_request_source_threshold_ms": 0,
    }
    if enable_http_request_source is not None:
        sentry_options["enable_http_request_source"] = enable_http_request_source

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


def test_request_source(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.stdlib.test_httplib"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/stdlib/test_httplib.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_request_source"


def test_request_source_with_module_in_search_path(sentry_init, capture_events):
    """
    Test that request source is relative to the path of the module it ran in
    """
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=0,
    )

    events = capture_events()

    with start_transaction(name="foo"):
        from httplib_helpers.helpers import get_request_with_connection

        conn = HTTPConnection("localhost", port=PORT)
        get_request_with_connection(conn, "/foo")

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "httplib_helpers.helpers"
    assert data.get(SPANDATA.CODE_FILEPATH) == "httplib_helpers/helpers.py"

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "get_request_with_connection"


def test_no_request_source_if_duration_too_short(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        enable_http_request_source=True,
        http_request_source_threshold_ms=100,
    )

    already_patched_putrequest = HTTPConnection.putrequest

    def overwrite_timestamps_putrequest(self, *args, **kwargs):
        already_patched_putrequest(self, *args, **kwargs)
        span = self._sentrysdk_span
        span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
        span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)

    HTTPConnection.putrequest = overwrite_timestamps_putrequest

    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


def test_request_source_if_duration_over_threshold(sentry_init, capture_events):
    sentry_init(
        enable_tracing=True,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    already_patched_putrequest = HTTPConnection.putrequest

    def overwrite_timestamps_putrequest(self, *args, **kwargs):
        already_patched_putrequest(self, *args, **kwargs)
        span = self._sentrysdk_span
        span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
        span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)

    HTTPConnection.putrequest = overwrite_timestamps_putrequest

    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
        conn.request("GET", "/foo")
        conn.getresponse()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("GET")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.stdlib.test_httplib"
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/stdlib/test_httplib.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert (
        data.get(SPANDATA.CODE_FUNCTION)
        == "test_query_source_if_duration_over_threshold"
    )


def test_span_origin(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, debug=True)
    events = capture_events()

    with start_transaction(name="foo"):
        conn = HTTPConnection("localhost", port=PORT)
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

    with pytest.raises(TimeoutError):
        with start_transaction(op="op", name="name"):
            conn = HTTPConnection("localhost", port=PORT)
            conn.request("GET", "/bla")
            conn.getresponse()

    (transaction_envelope,) = envelopes
    transaction = transaction_envelope.get_transaction_event()
    assert len(transaction["spans"]) == 1

    span = transaction["spans"][0]
    assert span["op"] == "http.client"
    assert span["description"] == f"GET http://localhost:{PORT}/bla"  # noqa: E231
