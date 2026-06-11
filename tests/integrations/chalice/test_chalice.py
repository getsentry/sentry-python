import time

import pytest
from chalice import BadRequestError, Chalice
from chalice.local import LambdaContext, LocalGateway
from pytest_chalice.handlers import RequestHandler

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.chalice import CHALICE_VERSION, ChaliceIntegration
from sentry_sdk.utils import parse_version


def _populate_lambda_context(context):
    fn = context.function_name
    context.invoked_function_arn = (
        f"arn:aws:lambda:us-east-1:123456789012:function:{fn}"
    )
    context.log_group_name = f"/aws/lambda/{fn}"
    context.log_stream_name = "2024/01/01/[$LATEST]abcdef1234567890"
    context.aws_request_id = "test-request-id-1234"
    return context


def _generate_lambda_context(self):
    # Monkeypatch of the function _generate_lambda_context
    # from the class LocalGateway
    # for mock the timeout
    # type: () -> LambdaContext
    if self._config.lambda_timeout is None:
        timeout = 10 * 1000
    else:
        timeout = self._config.lambda_timeout * 1000
    context = LambdaContext(
        function_name=self._config.function_name,
        memory_size=self._config.lambda_memory_size,
        max_runtime_ms=timeout,
    )
    return _populate_lambda_context(context)


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

    context = _populate_lambda_context(
        LambdaContext(*lambda_context_args, max_runtime_ms=10000, time_source=time)
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


def _make_span_streaming_app(sentry_init):
    sentry_init(
        integrations=[ChaliceIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    app = Chalice(app_name="sentry_chalice")

    @app.route("/message")
    def hi():
        capture_message("hi")
        return {"status": "ok"}

    @app.route("/boom")
    def boom():
        raise Exception("boom goes the dynamite!")

    LocalGateway._generate_lambda_context = _generate_lambda_context

    return app


def test_span_streaming_basic(
    sentry_init,
    capture_items,
):
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("span")

    response = client.get("/message", headers={"X-Custom-Header": "test-value"})
    assert response.status_code == 200

    sentry_sdk.flush()

    segment_spans = [s.payload for s in items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1
    span = segment_spans[0]

    attrs = span["attributes"]
    assert attrs["sentry.op"] == "function.aws"
    assert attrs["sentry.origin"] == "auto.function.chalice"
    assert attrs["sentry.span.source"] == "component"
    assert attrs["cloud.platform"] == "aws_lambda"
    assert attrs["cloud.provider"] == "aws"
    assert attrs["cloud.region"] == "us-east-1"
    assert (
        attrs["cloud.resource_id"]
        == "arn:aws:lambda:us-east-1:123456789012:function:api_handler"
    )
    assert (
        attrs["aws.lambda.invoked_arn"]
        == "arn:aws:lambda:us-east-1:123456789012:function:api_handler"
    )
    assert attrs["faas.name"] == "api_handler"
    assert attrs["faas.invocation_id"] == "test-request-id-1234"
    assert attrs["faas.version"] == "$LATEST"
    assert attrs["aws.log.group.names"] == ["/aws/lambda/api_handler"]
    assert attrs["aws.log.stream.names"] == ["2024/01/01/[$LATEST]abcdef1234567890"]
    assert attrs["http.request.method"] == "GET"
    assert attrs["http.request.header.x-custom-header"] == "test-value"
    assert span["status"] == "ok"


def test_span_streaming_continue_trace(
    sentry_init,
    capture_items,
):
    """When incoming headers contain sentry-trace, the standalone Chalice
    path (no AWS Lambda span) continues the trace."""
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("span")

    trace_id = "471a43a4192642f0b136d5159a501701"
    parent_span_id = "a00bc7e6637abd57"
    sentry_trace_header = f"{trace_id}-{parent_span_id}-1"

    response = client.get(
        "/message", headers={"sentry-trace": sentry_trace_header}
    )
    assert response.status_code == 200

    sentry_sdk.flush()

    segment_spans = [s.payload for s in items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1
    assert segment_spans[0]["trace_id"] == trace_id
    assert segment_spans[0]["parent_span_id"] == parent_span_id


def test_span_streaming_error(
    sentry_init,
    capture_items,
):
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("event", "span")

    response = client.get("/boom")
    assert response.status_code == 500

    sentry_sdk.flush()

    error_items = [i for i in items if i.type == "event"]
    span_items = [i for i in items if i.type == "span"]

    assert len(error_items) == 1
    assert len(span_items) >= 1

    segment_spans = [s.payload for s in span_items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1

    attrs = segment_spans[0]["attributes"]
    assert attrs["sentry.op"] == "function.aws"
    assert attrs["sentry.origin"] == "auto.function.chalice"
    assert segment_spans[0]["status"] == "error"


def test_span_streaming_error_flush_ordering(
    sentry_init,
    capture_items,
):
    """The handler's own client.flush() must send the segment span.

    If flush runs before the span ends, the segment won't be in the
    transport without an extra sentry_sdk.flush() call after the request.
    On Lambda, the worker can freeze right after the response, so there's
    no second chance.
    """
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("span")

    response = client.get("/boom")
    assert response.status_code == 500

    segment_spans = [s.payload for s in items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1


def test_span_streaming_existing_span(
    sentry_init,
    capture_items,
):
    """When a segment already exists (e.g. created by the AWS Lambda
    integration), Chalice decorates it instead of creating a duplicate."""
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(
        name="lambda_segment",
        parent_span=None,
        attributes={"sentry.origin": "auto.function.aws_lambda"},
    ):
        response = client.get("/message")
        assert response.status_code == 200

    sentry_sdk.flush()

    segment_spans = [s.payload for s in items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1
    span = segment_spans[0]

    attrs = span["attributes"]
    assert attrs["sentry.origin"] == "auto.function.chalice"
    assert attrs["sentry.op"] == "function.aws"
    assert attrs["faas.name"] == "api_handler"
    assert span["status"] == "ok"


def test_span_streaming_existing_span_error(
    sentry_init,
    capture_items,
):
    app = _make_span_streaming_app(sentry_init)
    client = RequestHandler(app)
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(
        name="lambda_segment",
        parent_span=None,
        attributes={"sentry.origin": "auto.function.aws_lambda"},
    ):
        response = client.get("/boom")
        assert response.status_code == 500

    sentry_sdk.flush()

    error_items = [i for i in items if i.type == "event"]
    assert len(error_items) == 1

    segment_spans = [
        s.payload for s in items if s.type == "span" and s.payload.get("is_segment")
    ]
    assert len(segment_spans) == 1
    assert segment_spans[0]["attributes"]["sentry.origin"] == "auto.function.chalice"
    assert segment_spans[0]["status"] == "error"


def test_span_streaming_existing_span_scheduled_event(
    sentry_init,
    lambda_context_args,
    capture_items,
):
    sentry_init(
        integrations=[ChaliceIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    app = Chalice(app_name="sentry_chalice")

    @app.schedule("rate(1 minutes)")
    def every_hour(event):
        raise Exception("schedule event!")

    items = capture_items("event", "span")

    context = _populate_lambda_context(
        LambdaContext(*lambda_context_args, max_runtime_ms=10000, time_source=time)
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
    with sentry_sdk.traces.start_span(
        name="lambda_segment",
        parent_span=None,
        attributes={"sentry.origin": "auto.function.aws_lambda"},
    ):
        with pytest.raises(Exception) as exc_info:
            every_hour(lambda_event, context=context)
        assert str(exc_info.value) == "schedule event!"

    sentry_sdk.flush()

    segment_spans = [
        s.payload for s in items if s.type == "span" and s.payload.get("is_segment")
    ]
    assert len(segment_spans) == 1
    assert segment_spans[0]["attributes"]["sentry.origin"] == "auto.function.chalice"
    assert segment_spans[0]["status"] == "error"


def test_span_streaming_scheduled_event(
    sentry_init,
    lambda_context_args,
    capture_items,
):
    sentry_init(
        integrations=[ChaliceIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )
    app = Chalice(app_name="sentry_chalice")

    @app.schedule("rate(1 minutes)")
    def every_hour(event):
        raise Exception("schedule event!")

    items = capture_items("event", "span")

    context = _populate_lambda_context(
        LambdaContext(*lambda_context_args, max_runtime_ms=10000, time_source=time)
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

    sentry_sdk.flush()

    span_items = [i for i in items if i.type == "span"]
    segment_spans = [s.payload for s in span_items if s.payload.get("is_segment")]
    assert len(segment_spans) == 1

    attrs = segment_spans[0]["attributes"]
    assert attrs["sentry.op"] == "function.aws"
    assert attrs["sentry.origin"] == "auto.function.chalice"
    assert attrs["sentry.span.source"] == "component"
    assert attrs["cloud.platform"] == "aws_lambda"
    assert attrs["cloud.provider"] == "aws"
    assert attrs["cloud.region"] == "us-east-1"
    assert (
        attrs["cloud.resource_id"]
        == "arn:aws:lambda:us-east-1:123456789012:function:lambda_name"
    )
    assert (
        attrs["aws.lambda.invoked_arn"]
        == "arn:aws:lambda:us-east-1:123456789012:function:lambda_name"
    )
    assert attrs["faas.name"] == "lambda_name"
    assert attrs["faas.invocation_id"] == "test-request-id-1234"
    assert attrs["faas.version"] == "$LATEST"
    assert attrs["aws.log.group.names"] == ["/aws/lambda/lambda_name"]
    assert attrs["aws.log.stream.names"] == ["2024/01/01/[$LATEST]abcdef1234567890"]
    assert segment_spans[0]["status"] == "error"
