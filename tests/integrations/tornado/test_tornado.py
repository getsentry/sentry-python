import pytest

from sentry_sdk import configure_scope

from sentry_sdk.integrations.tornado import TornadoIntegration

from tornado.web import RequestHandler, Application, HTTPError
from tornado.testing import AsyncHTTPTestCase


class CrashingHandler(RequestHandler):
    def get(self):
        with configure_scope() as scope:
            scope.set_tag("foo", 42)
        1 / 0


class TestBasic(AsyncHTTPTestCase):
    @pytest.fixture(autouse=True)
    def initialize(self, sentry_init, capture_events):
        sentry_init(integrations=[TornadoIntegration()])
        self.events = capture_events()

    def get_app(self):

        return Application([(r"/hi", CrashingHandler)])

    def test_basic(self):
        response = self.fetch("/hi?foo=bar")
        assert response.code == 500

        event, = self.events
        exception, = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"

        request = event["request"]
        host = request["headers"]["Host"]
        assert event["request"] == {
            "env": {"REMOTE_ADDR": "127.0.0.1"},
            "headers": {"Accept-Encoding": "gzip", "Connection": "close", "Host": host},
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


class Test400NotLogged(AsyncHTTPTestCase):
    @pytest.fixture(autouse=True)
    def initialize(self, sentry_init, capture_events):
        sentry_init(integrations=[TornadoIntegration()])
        self.events = capture_events()

    def get_app(self):
        class CrashingHandler(RequestHandler):
            def get(self):
                raise HTTPError(400, "Oops")

        return Application([(r"/", CrashingHandler)])

    def test_400_not_logged(self):
        response = self.fetch("/")
        assert response.code == 400

        assert not self.events


class TestUserAuth(AsyncHTTPTestCase):
    @pytest.fixture(autouse=True)
    def initialize(self, sentry_init, capture_events):
        sentry_init(integrations=[TornadoIntegration()], send_default_pii=True)
        self.events = capture_events()

    def get_app(self):
        class UserHandler(RequestHandler):
            def get(self):
                1 / 0

            def get_current_user(self):
                return 42

        class NoUserHandler(RequestHandler):
            def get(self):
                1 / 0

        return Application([(r"/auth", UserHandler), (r"/noauth", NoUserHandler)])

    def test_has_user(self):
        response = self.fetch("/auth")
        assert response.code == 500

        event, = self.events
        exception, = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"

        assert event["user"] == {"is_authenticated": True}

    def test_has_no_user(self):
        response = self.fetch("/noauth")
        assert response.code == 500

        event, = self.events
        exception, = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"

        assert "user" not in event
