from werkzeug.test import Client
import pytest

from sentry_sdk.integrations._wsgi import run_wsgi_app


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
        1 / 0

    return app


def test_basic(sentry_init, crashing_app, capture_events):
    sentry_init(send_default_pii=True)
    app = lambda environ, start_response: run_wsgi_app(
        crashing_app, environ, start_response
    )
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
    }
