import pytest

import sentry_sdk
from chalice import Chalice

import sentry_sdk.integrations.chalice as chalice_sentry

from pytest_chalice.handlers import RequestHandler

pytest.importorskip("chalice")

SENTRY_DSN = "https://111@sentry.io/111"


@pytest.fixture
def app():
    sentry_sdk.init(dsn=SENTRY_DSN, integrations=[chalice_sentry.ChaliceIntegration()])
    app = Chalice(app_name="sentry_chalice")

    @app.route("/boom")
    def boom():
        raise Exception("boom goes the dynamite!")

    @app.route("/context")
    def has_request():
        raise Exception("boom goes the dynamite!")

    return app


def test_exception_boom(app, client: RequestHandler) -> None:
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json == dict(
        [
            ("Code", "InternalServerError"),
            ("Message", "An internal server error occurred."),
        ]
    )


def test_has_request(app, capture_events, client: RequestHandler):
    events = capture_events()

    response = client.get("/context")
    assert response.status_code == 500

    (event,) = events
    assert event["transaction"] == "api_handler"
    assert "data" not in event["request"]
    assert event["request"]["url"] == "awslambda:///api_handler"
    assert event["request"]["headers"] == {}
