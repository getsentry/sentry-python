import pytest

from chalice import Chalice
from chalice.local import LambdaContext, LocalGateway

from sentry_sdk.integrations.chalice import ChaliceIntegration

from pytest_chalice.handlers import RequestHandler


def _generate_lambda_context(self):
    # Monkeypatch of the function _generate_lambda_context
    # from the class LocalGateway
    # for mock the timeout
    # type: () -> LambdaContext
    if self._config.lambda_timeout is None:
        timeout = 10 * 1000
    else:
        timeout = self._config.lambda_timeout * 1000
    return LambdaContext(
        function_name=self._config.function_name,
        memory_size=self._config.lambda_memory_size,
        max_runtime_ms=timeout
    )


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

    LocalGateway._generate_lambda_context = _generate_lambda_context

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
