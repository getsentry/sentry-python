
import pytest


pytest.importorskip("trytond")

from trytond.wsgi import app as trytond_app
from sentry_sdk import capture_message

from werkzeug.test import Client

from sentry_sdk.integrations.trytond import TrytondWSGIIntegration


@pytest.fixture(scope="function")
def app(sentry_init):
    app = trytond_app

    @app.route("/message")
    def hi():
        return "ok"

    yield app


@pytest.fixture
def get_client(app):
    def inner():
        return Client(app)

    return inner


def test_has_context(sentry_init, app, capture_events, get_client):
    sentry_init(integrations=[TrytondWSGIIntegration()])
    events = capture_events()

    client = get_client()
    response = client.get("/message")
    assert response[1] == "200 OK"

    event, = events
    assert event["message"] == "hi"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "http://localhost/message"

