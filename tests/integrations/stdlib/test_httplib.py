import json
import random
from textwrap import dedent

import pytest

try:
    # py3
    from urllib.request import urlopen
except ImportError:
    # py2
    from urllib import urlopen

try:
    # py2
    from httplib import HTTPConnection, HTTPSConnection
except ImportError:
    # py3
    from http.client import HTTPConnection, HTTPSConnection

try:
    # py3
    from urllib.parse import parse_qsl, urlencode
except ImportError:
    # py2
    from urlparse import parse_qsl  # type: ignore
    from urllib import urlencode  # type: ignore

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import MATCH_ALL, SPANDATA
from sentry_sdk.tracing import Transaction
from sentry_sdk.integrations.stdlib import StdlibIntegration

from tests.conftest import MockServerRequestHandler, create_mock_http_server

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
    assert crumb["data"] == {
        "url": url,
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "reason": "OK",
        SPANDATA.HTTP_FRAGMENT: "",
        SPANDATA.HTTP_QUERY: "",
    }


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
    assert crumb["data"] == {
        "url": url,
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "reason": "OK",
        "extra": "foo",
        SPANDATA.HTTP_FRAGMENT: "",
        SPANDATA.HTTP_QUERY: "",
    }


def test_empty_realurl(sentry_init, capture_events):
    """
    Ensure that after using sentry_sdk.init you can putrequest a
    None url.
    """

    sentry_init(dsn="")
    HTTPConnection("example.com", port=443).putrequest("POST", None)


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

    with pytest.raises(Exception):
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
    assert crumb["data"] == {
        "url": "http://localhost:{}/200".format(PORT),
        SPANDATA.HTTP_METHOD: "GET",
        SPANDATA.HTTP_STATUS_CODE: 200,
        "reason": "OK",
        SPANDATA.HTTP_FRAGMENT: "",
        SPANDATA.HTTP_QUERY: "",
    }


def test_outgoing_trace_headers(sentry_init, monkeypatch):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    sentry_init(traces_sample_rate=1.0)

    headers = {}
    headers["baggage"] = (
        "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
        "sentry-user_id=Am%C3%A9lie, other-vendor-value-2=foo;bar;"
    )

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

        expected_outgoing_baggage_items = [
            "sentry-trace_id=771a43a4192642f0b136d5159a501700",
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3",
            "sentry-sample_rate=0.01337",
            "sentry-user_id=Am%C3%A9lie",
        ]

        assert sorted(request_headers["baggage"].split(",")) == sorted(
            expected_outgoing_baggage_items
        )


def test_outgoing_trace_headers_head_sdk(sentry_init, monkeypatch):
    # HTTPSConnection.send is passed a string containing (among other things)
    # the headers on the request. Mock it so we can check the headers, and also
    # so it doesn't try to actually talk to the internet.
    mock_send = mock.Mock()
    monkeypatch.setattr(HTTPSConnection, "send", mock_send)

    # make sure transaction is always sampled
    monkeypatch.setattr(random, "random", lambda: 0.1)

    sentry_init(traces_sample_rate=0.5, release="foo")
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

        expected_outgoing_baggage_items = [
            "sentry-trace_id=%s" % transaction.trace_id,
            "sentry-sample_rate=0.5",
            "sentry-sampled=%s" % "true" if transaction.sampled else "false",
            "sentry-release=foo",
            "sentry-environment=production",
        ]

        assert sorted(request_headers["baggage"].split(",")) == sorted(
            expected_outgoing_baggage_items
        )


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


def test_graphql_get_client_error_captured(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[StdlibIntegration()])

    params = {"query": "query QueryName {user{name}}"}
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "some error",
                "locations": [{"line": 2, "column": 3}],
                "path": ["user"],
            }
        ],
    }

    events = capture_events()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_GET", do_GET):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("GET", "/graphql?" + urlencode(params))
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    (event,) = events

    assert event["request"]["url"] == "http://localhost:{}/graphql".format(PORT)
    assert event["request"]["method"] == "GET"
    assert dict(parse_qsl(event["request"]["query_string"])) == params
    assert "data" not in event["request"]
    assert event["contexts"]["response"]["data"] == graphql_response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["QueryName", "query", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: QueryName, type: query"
    )


def test_graphql_post_client_error_captured(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[StdlibIntegration()])

    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "already have too many pets",
                "locations": [{"line": 1, "column": 1}],
            }
        ],
    }

    events = capture_events()

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_POST", do_POST):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("POST", "/graphql", body=json.dumps(graphql_request).encode())
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    (event,) = events

    assert event["request"]["url"] == "http://localhost:{}/graphql".format(PORT)
    assert event["request"]["method"] == "POST"
    assert event["request"]["query_string"] == ""
    assert event["request"]["data"] == graphql_request
    assert event["contexts"]["response"]["data"] == graphql_response

    assert event["request"]["api_target"] == "graphql"
    assert event["fingerprint"] == ["AddPet", "mutation", 200]
    assert (
        event["exception"]["values"][0]["value"]
        == "GraphQL request failed, name: AddPet, type: mutation"
    )


def test_graphql_get_client_no_errors_returned(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[StdlibIntegration()])

    params = {"query": "query QueryName {user{name}}"}
    graphql_response = {
        "data": None,
    }

    events = capture_events()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_GET", do_GET):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("GET", "/graphql?" + urlencode(params))
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    assert not events


def test_graphql_post_client_no_errors_returned(sentry_init, capture_events):
    sentry_init(send_default_pii=True, integrations=[StdlibIntegration()])

    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
    }

    events = capture_events()

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_POST", do_POST):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("POST", "/graphql", body=json.dumps(graphql_request).encode())
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    assert not events


def test_graphql_no_get_errors_if_option_is_off(sentry_init, capture_events):
    sentry_init(
        send_default_pii=True,
        integrations=[StdlibIntegration(capture_graphql_errors=False)],
    )

    params = {"query": "query QueryName {user{name}}"}
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "some error",
                "locations": [{"line": 2, "column": 3}],
                "path": ["user"],
            }
        ],
    }

    events = capture_events()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_GET", do_GET):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("GET", "/graphql?" + urlencode(params))
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    assert not events


def test_graphql_no_post_errors_if_option_is_off(sentry_init, capture_events):
    sentry_init(
        send_default_pii=True,
        integrations=[StdlibIntegration(capture_graphql_errors=False)],
    )

    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }
    graphql_response = {
        "data": None,
        "errors": [
            {
                "message": "already have too many pets",
                "locations": [{"line": 1, "column": 1}],
            }
        ],
    }

    events = capture_events()

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(graphql_response).encode())

    with mock.patch.object(MockServerRequestHandler, "do_POST", do_POST):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("POST", "/graphql", body=json.dumps(graphql_request).encode())
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == json.dumps(graphql_response).encode()

    assert not events


def test_graphql_non_json_response(sentry_init, capture_events):
    sentry_init(
        send_default_pii=True,
        integrations=[StdlibIntegration()],
    )

    graphql_request = {
        "query": dedent(
            """
            mutation AddPet ($name: String!) {
                addPet(name: $name) {
                    id
                    name
                }
            }
        """
        ),
        "variables": {
            "name": "Lucy",
        },
    }

    events = capture_events()

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"not json")

    with mock.patch.object(MockServerRequestHandler, "do_POST", do_POST):
        conn = HTTPConnection("localhost:{}".format(PORT))
        conn.request("POST", "/graphql", body=json.dumps(graphql_request).encode())
        response = conn.getresponse()

    # make sure the response can still be read() normally
    assert response.read() == b"not json"

    assert not events
