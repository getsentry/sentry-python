from werkzeug.test import Client
import pytest

from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
        1 / 0

    return app


@pytest.fixture
def crashing_env_modifing_app():
    class TooSmartClass(object):
        def __init__(self, environ):
            self.environ = environ

        def __repr__(self):
            if "my_representation" in self.environ:
                return self.environ["my_representation"]

            self.environ["my_representation"] = "<This is me>"
            return self.environ["my_representation"]

    def app(environ, start_response):
        environ["tsc"] = TooSmartClass(environ)
        1 / 0

    return app


def test_basic(sentry_init, crashing_app, capture_events):
    sentry_init(send_default_pii=True)
    app = SentryWsgiMiddleware(crashing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    event, = events

    assert event["request"] == {
        "env": {"SERVER_NAME": "localhost", "SERVER_PORT": "80"},
        "headers": {"Content-Length": "0", "Content-Type": "", "Host": "localhost"},
        "method": "GET",
        "query_string": "",
        "url": "http://localhost/",
    }


def test_env_modifing_app(sentry_init, crashing_env_modifing_app, capture_events):
    sentry_init(send_default_pii=True)
    app = SentryWsgiMiddleware(crashing_env_modifing_app)
    client = Client(app)
    events = capture_events()

    with pytest.raises(ZeroDivisionError):
        client.get("/")

    assert len(events) == 1  # only one exception is raised
