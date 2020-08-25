import pytest

pytest.importorskip("chalice")
from chalice import Chalice

from sentry_sdk.integrations.chalice import ChaliceIntegration

from pytest_chalice.handlers import RequestHandler


@pytest.fixture
def app(sentry_init):
    sentry_init(integrations=[ChaliceIntegration()])
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
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"
