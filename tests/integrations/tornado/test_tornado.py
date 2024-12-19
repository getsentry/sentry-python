import json
import re

import pytest

import sentry_sdk
from sentry_sdk import start_span, capture_message
from sentry_sdk.integrations.tornado import TornadoIntegration

from tornado.web import RequestHandler, Application, HTTPError
from tornado.testing import AsyncHTTPTestCase


@pytest.fixture
def tornado_testcase(request):
    # Take the unittest class provided by tornado and manually call its setUp
    # and tearDown.
    #
    # The pytest plugins for tornado seem too complicated to use, as they for
    # some reason assume I want to write my tests in async code.
    def inner(app):
        class TestBogus(AsyncHTTPTestCase):
            def get_app(self):
                return app

            def bogustest(self):
                # We need to pass a valid test method name to the ctor, so this
                # is the method. It does nothing.
                pass

        self = TestBogus("bogustest")
        self.setUp()
        request.addfinalizer(self.tearDown)
        return self

    return inner


class CrashingHandler(RequestHandler):
    def get(self):
        sentry_sdk.get_isolation_scope().set_tag("foo", "42")
        1 / 0

    def post(self):
        sentry_sdk.get_isolation_scope().set_tag("foo", "43")
        1 / 0


class CrashingWithMessageHandler(RequestHandler):
    def get(self):
        capture_message("hi")
        1 / 0


class HelloHandler(RequestHandler):
    async def get(self):
        sentry_sdk.get_isolation_scope().set_tag("foo", "42")

        return b"hello"

    async def post(self):
        sentry_sdk.get_isolation_scope().set_tag("foo", "43")

        return b"hello"


def test_basic(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
    events = capture_events()
    client = tornado_testcase(Application([(r"/hi", CrashingHandler)]))

    response = client.fetch(
        "/hi?foo=bar", headers={"Cookie": "name=value; name2=value2; name3=value3"}
    )
    assert response.code == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "tornado"

    request = event["request"]
    host = request["headers"]["Host"]
    assert event["request"] == {
        "env": {"REMOTE_ADDR": "127.0.0.1"},
        "headers": {
            "Accept-Encoding": "gzip",
            "Connection": "close",
            "Cookie": "name=value; name2=value2; name3=value3",
            **request["headers"],
        },
        "cookies": {"name": "value", "name2": "value2", "name3": "value3"},
        "method": "GET",
        "query_string": "foo=bar",
        "url": "http://{host}/hi".format(host=host),
    }

    assert event["tags"] == {"foo": "42"}
    assert (
        event["transaction"]
        == "tests.integrations.tornado.test_tornado.CrashingHandler.get"
    )
    assert event["transaction_info"] == {"source": "component"}

    assert not sentry_sdk.get_isolation_scope()._tags


@pytest.mark.parametrize(
    "handler,code",
    [
        (CrashingHandler, 500),
        (HelloHandler, 200),
    ],
)
def test_transactions(tornado_testcase, sentry_init, capture_events, handler, code):
    sentry_init(integrations=[TornadoIntegration()], traces_sample_rate=1.0)
    events = capture_events()
    client = tornado_testcase(Application([(r"/hi", handler)]))

    with start_span(name="client") as span:
        pass

    response = client.fetch(
        "/hi", method="POST", body=b"heyoo", headers=dict(span.iter_headers())
    )
    assert response.code == code

    if code == 200:
        client_tx, server_tx = events
        server_error = None
    else:
        client_tx, server_error, server_tx = events

    assert client_tx["type"] == "transaction"
    assert client_tx["transaction"] == "client"
    assert client_tx["transaction_info"] == {
        "source": "custom"
    }  # because this is just the start_span() above.

    if server_error is not None:
        assert server_error["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert (
            server_error["transaction"]
            == "tests.integrations.tornado.test_tornado.CrashingHandler.post"
        )
        assert server_error["transaction_info"] == {"source": "component"}

    if code == 200:
        assert (
            server_tx["transaction"]
            == "tests.integrations.tornado.test_tornado.HelloHandler.post"
        )
    else:
        assert (
            server_tx["transaction"]
            == "tests.integrations.tornado.test_tornado.CrashingHandler.post"
        )

    assert server_tx["transaction_info"] == {"source": "component"}
    assert server_tx["type"] == "transaction"

    request = server_tx["request"]
    host = request["headers"]["Host"]
    assert server_tx["request"] == {
        "env": {"REMOTE_ADDR": "127.0.0.1"},
        "headers": {
            "Accept-Encoding": "gzip",
            "Connection": "close",
            **request["headers"],
        },
        "method": "POST",
        "query_string": "",
        "data": {"heyoo": [""]},
        "url": "http://{host}/hi".format(host=host),
    }

    assert (
        client_tx["contexts"]["trace"]["trace_id"]
        == server_tx["contexts"]["trace"]["trace_id"]
    )

    if server_error is not None:
        assert (
            server_error["contexts"]["trace"]["trace_id"]
            == server_tx["contexts"]["trace"]["trace_id"]
        )


def test_400_not_logged(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()])
    events = capture_events()

    class CrashingHandler(RequestHandler):
        def get(self):
            raise HTTPError(400, "Oops")

    client = tornado_testcase(Application([(r"/", CrashingHandler)]))

    response = client.fetch("/")
    assert response.code == 400

    assert not events


def test_user_auth(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
    events = capture_events()

    class UserHandler(RequestHandler):
        def get(self):
            1 / 0

        def get_current_user(self):
            return 42

    class NoUserHandler(RequestHandler):
        def get(self):
            1 / 0

    client = tornado_testcase(
        Application([(r"/auth", UserHandler), (r"/noauth", NoUserHandler)])
    )

    # has user
    response = client.fetch("/auth")
    assert response.code == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    assert event["user"] == {"is_authenticated": True}

    events.clear()

    # has no user
    response = client.fetch("/noauth")
    assert response.code == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    assert "user" not in event


def test_formdata(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
    events = capture_events()

    class FormdataHandler(RequestHandler):
        def post(self):
            raise ValueError(json.dumps(sorted(self.request.body_arguments)))

    client = tornado_testcase(Application([(r"/form", FormdataHandler)]))

    response = client.fetch(
        "/form?queryarg=1",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=b"field1=value1&field2=value2",
    )

    assert response.code == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["value"] == '["field1", "field2"]'
    assert event["request"]["data"] == {"field1": ["value1"], "field2": ["value2"]}


def test_json(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
    events = capture_events()

    class FormdataHandler(RequestHandler):
        def post(self):
            raise ValueError(json.dumps(sorted(self.request.body_arguments)))

    client = tornado_testcase(Application([(r"/form", FormdataHandler)]))

    response = client.fetch(
        "/form?queryarg=1",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=b"""
        {"foo": {"bar": 42}}
        """,
    )

    assert response.code == 500

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["value"] == "[]"
    assert event
    assert event["request"]["data"] == {"foo": {"bar": 42}}


def test_error_has_new_trace_context_performance_enabled(
    tornado_testcase, sentry_init, capture_events
):
    """
    Check if an 'trace' context is added to errros and transactions when performance monitoring is enabled.
    """
    sentry_init(
        integrations=[TornadoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    client = tornado_testcase(Application([(r"/hi", CrashingWithMessageHandler)]))
    client.fetch("/hi")

    (msg_event, error_event, transaction_event) = events

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert "trace" in transaction_event["contexts"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
    )


def test_error_has_new_trace_context_performance_disabled(
    tornado_testcase, sentry_init, capture_events
):
    """
    Check if an 'trace' context is added to errros and transactions when performance monitoring is disabled.
    """
    sentry_init(
        integrations=[TornadoIntegration()],
        traces_sample_rate=None,  # this is the default, just added for clarity
    )
    events = capture_events()

    client = tornado_testcase(Application([(r"/hi", CrashingWithMessageHandler)]))
    client.fetch("/hi")

    (msg_event, error_event) = events

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
    )


def test_error_has_existing_trace_context_performance_enabled(
    tornado_testcase, sentry_init, capture_events
):
    """
    Check if an 'trace' context is added to errros and transactions
    from the incoming 'sentry-trace' header when performance monitoring is enabled.
    """
    sentry_init(
        integrations=[TornadoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    headers = {"sentry-trace": sentry_trace_header}

    client = tornado_testcase(Application([(r"/hi", CrashingWithMessageHandler)]))
    client.fetch("/hi", headers=headers)

    (msg_event, error_event, transaction_event) = events

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert "trace" in transaction_event["contexts"]
    assert "trace_id" in transaction_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == transaction_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )


def test_error_has_existing_trace_context_performance_disabled(
    tornado_testcase, sentry_init, capture_events
):
    """
    Check if an 'trace' context is added to errros and transactions
    from the incoming 'sentry-trace' header when performance monitoring is disabled.
    """
    sentry_init(
        integrations=[TornadoIntegration()],
        traces_sample_rate=None,  # this is the default, just added for clarity
    )
    events = capture_events()

    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "6e8f22c393e68f19"
    parent_sampled = 1
    sentry_trace_header = "{}-{}-{}".format(trace_id, parent_span_id, parent_sampled)

    headers = {"sentry-trace": sentry_trace_header}

    client = tornado_testcase(Application([(r"/hi", CrashingWithMessageHandler)]))
    client.fetch("/hi", headers=headers)

    (msg_event, error_event) = events

    assert "trace" in msg_event["contexts"]
    assert "trace_id" in msg_event["contexts"]["trace"]

    assert "trace" in error_event["contexts"]
    assert "trace_id" in error_event["contexts"]["trace"]

    assert (
        msg_event["contexts"]["trace"]["trace_id"]
        == error_event["contexts"]["trace"]["trace_id"]
        == "471a43a4192642f0b136d5159a501701"
    )


def test_span_origin(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], traces_sample_rate=1.0)
    events = capture_events()
    client = tornado_testcase(Application([(r"/hi", CrashingHandler)]))

    client.fetch(
        "/hi?foo=bar", headers={"Cookie": "name=value; name2=value2; name3=value3"}
    )

    (_, event) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.tornado"


def test_attributes_in_traces_sampler(tornado_testcase, sentry_init):
    def traces_sampler(sampling_context):
        assert sampling_context["url.query"] == "foo=bar"
        assert sampling_context["url.path"] == "/hi"
        assert sampling_context["url.scheme"] == "http"
        assert re.match(
            r"http:\/\/127\.0\.0\.1:[0-9]{4,5}\/hi\?foo=bar",
            sampling_context["url.full"],
        )
        assert sampling_context["http.request.method"] == "GET"
        assert sampling_context["server.address"] == "127.0.0.1"
        assert sampling_context["server.port"].isnumeric()
        assert sampling_context["network.protocol.name"] == "HTTP"
        assert sampling_context["network.protocol.version"] == "1.1"
        assert sampling_context["http.request.header.custom-header"] == "Custom Value"

        return True

    sentry_init(
        integrations=[TornadoIntegration],
        traces_sampler=traces_sampler,
    )

    client = tornado_testcase(Application([(r"/hi", HelloHandler)]))
    client.fetch("/hi?foo=bar", headers={"Custom-Header": "Custom Value"})
