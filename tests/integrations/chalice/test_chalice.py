import pytest
import time
from chalice import Chalice, BadRequestError
from chalice.local import LambdaContext, LocalGateway

from sentry_sdk import capture_message
from sentry_sdk.integrations.chalice import CHALICE_VERSION, ChaliceIntegration
from sentry_sdk.utils import parse_version

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
        max_runtime_ms=timeout,
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

    @app.route("/badrequest")
    def badrequest():
        raise BadRequestError("bad-request")

    @app.route("/message")
    def hi():
        capture_message("hi")
        return {"status": "ok"}

    @app.route("/message/{message_id}")
    def hi_with_id(message_id):
        capture_message("hi again")
        return {"status": "ok"}

    LocalGateway._generate_lambda_context = _generate_lambda_context

    return app


@pytest.fixture
def lambda_context_args():
    return ["lambda_name", 256]


def test_exception_boom(app, client: RequestHandler) -> None:
    response = client.get("/boom")
    assert response.status_code == 500
    assert response.json == {
        "Code": "InternalServerError",
        "Message": "An internal server error occurred.",
    }


def test_has_request(app, capture_events, client: RequestHandler):
    events = capture_events()

    response = client.get("/context")
    assert response.status_code == 500

    (event,) = events
    assert event["level"] == "error"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "Exception"


def test_scheduled_event(app, lambda_context_args):
    @app.schedule("rate(1 minutes)")
    def every_hour(event):
        raise Exception("schedule event!")

    context = LambdaContext(
        *lambda_context_args, max_runtime_ms=10000, time_source=time
    )

    lambda_event = {
        "version": "0",
        "account": "120987654312",
        "region": "us-west-1",
        "detail": {},
        "detail-type": "Scheduled Event",
        "source": "aws.events",
        "time": "1970-01-01T00:00:00Z",
        "id": "event-id",
        "resources": ["arn:aws:events:us-west-1:120987654312:rule/my-schedule"],
    }
    with pytest.raises(Exception) as exc_info:
        every_hour(lambda_event, context=context)
    assert str(exc_info.value) == "schedule event!"


@pytest.mark.skipif(
    parse_version(CHALICE_VERSION) >= (1, 26, 0),
    reason="different behavior based on chalice version",
)
def test_bad_request_old(client: RequestHandler) -> None:
    response = client.get("/badrequest")

    assert response.status_code == 400
    assert response.json == {
        "Code": "BadRequestError",
        "Message": "BadRequestError: bad-request",
    }


@pytest.mark.skipif(
    parse_version(CHALICE_VERSION) < (1, 26, 0),
    reason="different behavior based on chalice version",
)
def test_bad_request(client: RequestHandler) -> None:
    response = client.get("/badrequest")

    assert response.status_code == 400
    assert response.json == {
        "Code": "BadRequestError",
        "Message": "bad-request",
    }


@pytest.mark.parametrize(
    "url,expected_transaction,expected_source",
    [
        ("/message", "api_handler", "component"),
        ("/message/123456", "api_handler", "component"),
    ],
)
def test_transaction(
    app,
    client: RequestHandler,
    capture_events,
    url,
    expected_transaction,
    expected_source,
):
    events = capture_events()

    response = client.get(url)
    assert response.status_code == 200

    (event,) = events
    assert event["transaction"] == expected_transaction
    assert event["transaction_info"] == {"source": expected_source}
