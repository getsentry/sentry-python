import sentry_sdk
from sentry_sdk.integrations.aws_lambda import (
    AwsLambdaIntegration,
    _get_messaging_batch_count,
    _wrap_handler,
)


class MockContext:
    function_name = "test_function"
    function_version = "$LATEST"
    invoked_function_arn = (
        "arn:aws:lambda:us-east-1:123456789012:function:test_function"
    )
    aws_request_id = "test-request-id"
    log_group_name = "/aws/lambda/test_function"
    log_stream_name = "2026/10/29/[$LATEST]test"

    def get_remaining_time_in_millis(self):
        return 300000


def test_get_messaging_batch_count_sqs():
    single_sqs = {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "messageId": "1",
                "body": "hello",
            }
        ]
    }
    assert _get_messaging_batch_count(single_sqs) == 1

    batch_sqs = {
        "Records": [{"eventSource": "aws:sqs", "messageId": str(i)} for i in range(5)]
    }
    assert _get_messaging_batch_count(batch_sqs) == 5

    # Case insensitive EventSource
    capitalized_sqs = {
        "Records": [
            {
                "EventSource": "aws:sqs",
                "messageId": "1",
            }
        ]
    }
    assert _get_messaging_batch_count(capitalized_sqs) == 1


def test_get_messaging_batch_count_sns():
    single_sns = {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {"Message": "hello"},
            }
        ]
    }
    assert _get_messaging_batch_count(single_sns) == 1

    batch_sns = {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {"Message": f"msg {i}"},
            }
            for i in range(3)
        ]
    }
    assert _get_messaging_batch_count(batch_sns) == 3


def test_get_messaging_batch_count_non_messaging():
    assert _get_messaging_batch_count({}) is None
    assert _get_messaging_batch_count({"httpMethod": "GET", "path": "/"}) is None
    assert _get_messaging_batch_count([{"key": "val"}]) is None
    assert _get_messaging_batch_count("string_event") is None
    assert _get_messaging_batch_count({"Records": []}) is None

    # S3 event
    s3_event = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {"bucket": {"name": "my-bucket"}},
            }
        ]
    }
    assert _get_messaging_batch_count(s3_event) is None

    # DynamoDB event
    dynamo_event = {
        "Records": [
            {
                "eventSource": "aws:dynamodb",
                "eventName": "INSERT",
            }
        ]
    }
    assert _get_messaging_batch_count(dynamo_event) is None

    # Kinesis event
    kinesis_event = {
        "Records": [
            {
                "eventSource": "aws:kinesis",
                "kinesis": {"data": "dGVzdA=="},
            }
        ]
    }
    assert _get_messaging_batch_count(kinesis_event) is None


def test_handler_sqs_batch_span_streaming(sentry_init, capture_items):
    sentry_init(
        integrations=[AwsLambdaIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    sqs_event = {
        "Records": [
            {"eventSource": "aws:sqs", "messageId": "1"},
            {"eventSource": "aws:sqs", "messageId": "2"},
            {"eventSource": "aws:sqs", "messageId": "3"},
        ]
    }

    tags_recorded = {}

    @_wrap_handler
    def handler(event, context):
        tags_recorded.update(sentry_sdk.get_isolation_scope()._tags)
        return "ok"

    res = handler(sqs_event, MockContext())
    assert res == "ok"

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 1
    span = spans[0]

    assert span["attributes"]["messaging.batch.message_count"] == 3
    assert tags_recorded.get("batch_request") is True
    assert tags_recorded.get("batch_size") == 3


def test_handler_sns_single_span_streaming(sentry_init, capture_items):
    sentry_init(
        integrations=[AwsLambdaIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    sns_event = {
        "Records": [
            {"EventSource": "aws:sns", "Sns": {"Message": "hi"}},
        ]
    }

    tags_recorded = {}

    @_wrap_handler
    def handler(event, context):
        tags_recorded.update(sentry_sdk.get_isolation_scope()._tags)
        return "ok"

    res = handler(sns_event, MockContext())
    assert res == "ok"

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 1
    span = spans[0]

    assert span["attributes"]["messaging.batch.message_count"] == 1
    assert "batch_request" not in tags_recorded
    assert "batch_size" not in tags_recorded


def test_handler_non_messaging_span_streaming(sentry_init, capture_items):
    sentry_init(
        integrations=[AwsLambdaIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    http_event = {"httpMethod": "GET", "path": "/hello"}

    tags_recorded = {}

    @_wrap_handler
    def handler(event, context):
        tags_recorded.update(sentry_sdk.get_isolation_scope()._tags)
        return "ok"

    res = handler(http_event, MockContext())
    assert res == "ok"

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 1
    span = spans[0]

    assert "messaging.batch.message_count" not in span["attributes"]
    assert "batch_request" not in tags_recorded


def test_handler_top_level_list_span_streaming(sentry_init, capture_items):
    sentry_init(
        integrations=[AwsLambdaIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    list_event = [{"data": 1}, {"data": 2}]

    tags_recorded = {}

    @_wrap_handler
    def handler(event, context):
        tags_recorded.update(sentry_sdk.get_isolation_scope()._tags)
        return "ok"

    res = handler(list_event, MockContext())
    assert res == "ok"

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 1
    span = spans[0]

    # Non-messaging top-level list should not have messaging.batch.message_count
    assert "messaging.batch.message_count" not in span["attributes"]
    # But batch_request tags are set because len(list_event) > 1
    assert tags_recorded.get("batch_request") is True
    assert tags_recorded.get("batch_size") == 2
