import json

import pytest

from sentry_sdk import configure_scope
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
        with configure_scope() as scope:
            scope.set_tag("foo", 42)
        1 / 0


def test_basic(tornado_testcase, sentry_init, capture_events):
    sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
    events = capture_events()
    client = tornado_testcase(Application([(r"/hi", CrashingHandler)]))

    response = client.fetch(
        "/hi?foo=bar", headers={"Cookie": "name=value; name2=value2; name3=value3"}
    )
    assert response.code == 500

    event, = events
    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "tornado"

    request = event["request"]
    host = request["headers"]["Host"]
    assert event["request"] == {
        "env": {"REMOTE_ADDR": "127.0.0.1"},
        "headers": {
            "Accept-Encoding": "gzip",
            "Connection": "close",
            "Host": host,
            "Cookie": "name=value; name2=value2; name3=value3",
        },
        "cookies": {"name": "value", "name2": "value2", "name3": "value3"},
        "method": "GET",
        "query_string": "foo=bar",
        "url": "http://{host}/hi".format(host=host),
    }

    assert event["tags"] == {"foo": 42}
    assert (
        event["transaction"]
        == "tests.integrations.tornado.test_tornado.CrashingHandler.get"
    )

    with configure_scope() as scope:
        assert not scope._tags


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

    event, = events
    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    assert event["user"] == {"is_authenticated": True}

    events.clear()

    # has no user
    response = client.fetch("/noauth")
    assert response.code == 500

    event, = events
    exception, = event["exception"]["values"]
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

    event, = events
    exception, = event["exception"]["values"]
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

    event, = events
    exception, = event["exception"]["values"]
    assert exception["value"] == "[]"
    assert event
    assert event["request"]["data"] == {"foo": {"bar": 42}}
