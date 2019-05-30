import logging

from werkzeug.test import Client
import pytest

from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware


@pytest.fixture
def crashing_app():
    def app(environ, start_response):
        1 / 0

    return app


class IterableApp(object):
    def __init__(self, iterable):
        self.iterable = iterable

    def __call__(self, environ, start_response):
        return self.iterable


class ExitingIterable(object):
    def __init__(self, exc_func):
        self._exc_func = exc_func

    def __iter__(self):
        return self

    def __next__(self):
        raise self._exc_func()

    def next(self):
        return type(self).__next__(self)


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


def test_systemexit_0_is_ignored(sentry_init, capture_events):
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: SystemExit(0))
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(SystemExit):
        client.get("/")

    assert len(events) == 0


def test_systemexit_1_is_captured(sentry_init, capture_events):
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: SystemExit(1))
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(SystemExit):
        client.get("/")

    event, = events

    assert "exception" in event
    exc = event["exception"]["values"][-1]
    assert exc["type"] == "SystemExit"
    assert exc["value"] == "1"
    assert event["level"] == "error"


def test_keyboard_interrupt_is_captured(sentry_init, capture_events):
    sentry_init(send_default_pii=True)
    iterable = ExitingIterable(lambda: KeyboardInterrupt())
    app = SentryWsgiMiddleware(IterableApp(iterable))
    client = Client(app)
    events = capture_events()

    with pytest.raises(KeyboardInterrupt):
        client.get("/")

    event, = events

    assert "exception" in event
    exc = event["exception"]["values"][-1]
    assert exc["type"] == "KeyboardInterrupt"
    assert exc["value"] == ""
    assert event["level"] == "error"
