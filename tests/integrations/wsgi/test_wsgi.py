from werkzeug.test import Client
import pytest

from sentry_sdk import Hub

from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware, _ScopePoppingResponse


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
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
    }


def test_calls_close():
    hub = Hub.current
    stack_size = len(hub._stack)
    closes = []

    hub.push_scope()

    class Foo(object):
        def __iter__(self):
            yield 1
            yield 2
            yield 3

        def close(self):
            closes.append(1)

    response = _ScopePoppingResponse(hub, Foo())
    list(response)
    response.close()
    response.close()
    response.close()

    # multiple close calls are just forwarded, but the scope is only popped once
    assert len(closes) == 3
    assert len(hub._stack) == stack_size
