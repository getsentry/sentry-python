import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory


@pytest.mark.parametrize(
    "method,params,response,queue_url,account_id,resource_id",
    [
        pytest.param(
            "delete_queue",
            {"QueueUrl": "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"},
            {},
            "https://sqs.eu-north-1.amazonaws.com/123456789012/orders",
            "123456789012",
            None,
            id="queue-url",
        ),
        pytest.param(
            "create_queue",
            {"QueueName": "orders"},
            {"QueueUrl": "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"},
            None,
            None,
            None,
            id="create-queue",
        ),
        pytest.param(
            "get_queue_url",
            {"QueueName": "orders", "QueueOwnerAWSAccountId": "123456789012"},
            {"QueueUrl": "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"},
            None,
            "123456789012",
            None,
            id="get-queue-url",
        ),
        pytest.param(
            "start_message_move_task",
            {"SourceArn": "arn:aws:sqs:eu-north-1:123456789012:orders"},
            {"TaskHandle": "task-handle"},
            None,
            "123456789012",
            "arn:aws:sqs:eu-north-1:123456789012:orders",
            id="source-arn",
        ),
    ],
)
def test_request_attributes(
    client_factory,
    capture_items,
    method,
    params,
    response,
    queue_url,
    account_id,
    resource_id,
):
    client = client_factory("sqs")
    require_botocore_model_fields(client, method)

    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.RPC]
    attributes = span["attributes"]
    assert attributes[SPANDATA.MESSAGING_DESTINATION_NAME] == "orders"
    assert attributes.get(SPANDATA.AWS_SQS_QUEUE_URL) == queue_url
    assert attributes.get(SPANDATA.CLOUD_ACCOUNT_ID) == account_id
    assert attributes.get(SPANDATA.CLOUD_RESOURCE_ID) == resource_id
    assert SPANDATA.MESSAGING_SYSTEM not in attributes


def test_send_message(client_factory, capture_items):
    client = client_factory("sqs")

    with Stubber(client) as stubber:
        stubber.add_response(
            "send_message", {"MessageId": "5fea7756-0ea4-451a-a703-a558b933e274"}
        )
        spans = capture_spans_by_op(
            lambda: client.send_message(
                QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders",
                MessageBody="hi",
            ),
            capture_items,
        )

    (span,) = spans[OP.QUEUE_PUBLISH]
    assert span["name"] == "send orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "producer"
    assert attributes[SPANDATA.MESSAGING_SYSTEM] == "aws_sqs"
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "send"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "send"
    assert attributes[SPANDATA.MESSAGING_DESTINATION_NAME] == "orders"
    assert (
        attributes[SPANDATA.MESSAGING_MESSAGE_ID]
        == "5fea7756-0ea4-451a-a703-a558b933e274"
    )
    assert SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT not in attributes


@pytest.mark.parametrize(
    "failed,error_type",
    [
        pytest.param([], None, id="ok"),
        pytest.param(
            [{"Id": "2", "Code": "InternalError", "SenderFault": False}],
            "aws_batch_partial_failure",
            id="partial-failure",
        ),
    ],
)
def test_send_message_batch(client_factory, capture_items, failed, error_type):
    client = client_factory("sqs")

    with Stubber(client) as stubber:
        stubber.add_response(
            "send_message_batch",
            {
                "Successful": [
                    {
                        "Id": "1",
                        "MessageId": "5fea7756",
                        "MD5OfMessageBody": "49f68a5c8493ec2c0bf489821c21fc3b",
                    }
                ],
                "Failed": failed,
            },
        )
        spans = capture_spans_by_op(
            lambda: client.send_message_batch(
                QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders",
                Entries=[
                    {"Id": "1", "MessageBody": "hi"},
                    {"Id": "2", "MessageBody": "hi"},
                ],
            ),
            capture_items,
        )

    (span,) = spans[OP.QUEUE_PUBLISH]
    assert span["name"] == "send orders"
    assert span["status"] == ("ok" if error_type is None else "error")
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "producer"
    assert attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] == 2
    assert attributes.get(SPANDATA.ERROR_TYPE) == error_type
    assert SPANDATA.MESSAGING_MESSAGE_ID not in attributes


@pytest.mark.parametrize(
    "response,message_count",
    [
        # `Messages` is absent when the queue is empty.
        pytest.param({}, 0, id="empty"),
        pytest.param(
            {
                "Messages": [
                    {"MessageId": "1", "ReceiptHandle": "receipt-handle-1"},
                    {"MessageId": "2", "ReceiptHandle": "receipt-handle-2"},
                ]
            },
            2,
            id="messages",
        ),
    ],
)
def test_receive_message(client_factory, capture_items, response, message_count):
    client = client_factory("sqs")

    with Stubber(client) as stubber:
        stubber.add_response("receive_message", response)
        spans = capture_spans_by_op(
            lambda: client.receive_message(
                QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders",
                MaxNumberOfMessages=10,
            ),
            capture_items,
        )

    (span,) = spans[OP.QUEUE_RECEIVE]
    assert span["name"] == "receive orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "client"
    assert attributes[SPANDATA.MESSAGING_SYSTEM] == "aws_sqs"
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "receive"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "receive"
    assert attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] == message_count
    assert SPANDATA.MESSAGING_MESSAGE_ID not in attributes
    assert "receipt-handle" not in str(span)


def test_receive_message_error(client_factory, capture_items):
    client = client_factory("sqs")

    with Stubber(client) as stubber:
        stubber.add_client_error(
            "receive_message",
            service_error_code="AWS.SimpleQueueService.NonExistentQueue",
            http_status_code=400,
        )

        def call():
            with pytest.raises(ClientError):
                client.receive_message(
                    QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders"
                )

        spans = capture_spans_by_op(call, capture_items)

    (span,) = spans[OP.QUEUE_RECEIVE]
    assert span["status"] == "error"
    assert SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT not in span["attributes"]


@pytest.mark.parametrize(
    "method,params,response,message_count",
    [
        pytest.param(
            "delete_message",
            {"ReceiptHandle": "receipt-handle-1"},
            {},
            None,
            id="single",
        ),
        pytest.param(
            "delete_message_batch",
            {
                "Entries": [
                    {"Id": "1", "ReceiptHandle": "receipt-handle-1"},
                    {"Id": "2", "ReceiptHandle": "receipt-handle-2"},
                ]
            },
            {"Successful": [{"Id": "1"}, {"Id": "2"}], "Failed": []},
            2,
            id="batch",
        ),
    ],
)
def test_delete_message(
    client_factory, capture_items, method, params, response, message_count
):
    client = client_factory("sqs")

    with Stubber(client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(
                QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders",
                **params,
            ),
            capture_items,
        )

    (span,) = spans[OP.QUEUE_SETTLE]
    assert span["name"] == "settle orders"
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "client"
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "settle"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "settle"
    assert attributes.get(SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT) == message_count
    assert "receipt-handle" not in str(span)
