import pytest
import sentry_sdk
from chalice import Chalice

from sentry_chalice import ChaliceIntegration

from pytest_chalice.handlers import RequestHandler

SENTRY_DSN = 'https://111@sentry.io/111'


@pytest.fixture
def app():
    sentry_sdk.init(dsn=SENTRY_DSN, integrations=[ChaliceIntegration()])
    app = Chalice(app_name='sentry_chalice')

    @app.route('/boom')
    def boom():
        raise Exception('boom goes the dynamite!')

    @app.route('/context')
    def has_request():
        raise Exception('boom goes the dynamite!')

    return app


@pytest.fixture
def capture_events(monkeypatch):
    def inner():
        events = []
        test_client = sentry_sdk.Hub.current.client
        old_capture_event = test_client.transport.capture_event
        old_capture_envelope = test_client.transport.capture_envelope

        def append_event(event):
            events.append(event)
            return old_capture_event(event)

        def append_envelope(envelope):
            for item in envelope:
                if item.headers.get("type") in ("event", "transaction"):
                    events.append(item.payload.json)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(
            test_client.transport, "capture_event", append_event
        )
        monkeypatch.setattr(
            test_client.transport, "capture_envelope", append_envelope
        )
        return events

    return inner


def test_exception_boom(app, client: RequestHandler) -> None:
    response = client.get('/boom')
    assert response.status_code == 500
    assert response.json == dict(
        [
            ('Code', 'InternalServerError'),
            ('Message', 'An internal server error occurred.'),
        ]
    )


def test_has_request(app, capture_events, client: RequestHandler):
    events = capture_events()

    response = client.get('/context')
    assert response.status_code == 500

    (event,) = events
    assert event["transaction"] == "api_handler"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "awslambda:///api_handler"
    assert event["request"]["headers"] == {}
