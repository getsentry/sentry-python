import copy
import json

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory

_BATCH_ENTRIES = [
    {"Id": "1", "MessageBody": "hi"},
    {"Id": "2", "MessageBody": "hi"},
]


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


@pytest.mark.parametrize(
    "method,params,response,message_count,message_id,error_type",
    [
        pytest.param(
            "send_message",
            {"MessageBody": "hi"},
            {"MessageId": "5fea7756-0ea4-451a-a703-a558b933e274"},
            None,
            "5fea7756-0ea4-451a-a703-a558b933e274",
            None,
            id="single",
        ),
        pytest.param(
            "send_message_batch",
            {"Entries": _BATCH_ENTRIES},
            {
                "Successful": [
                    {
                        "Id": "1",
                        "MessageId": "message-1",
                        "MD5OfMessageBody": "49f68a5c8493ec2c0bf489821c21fc3b",
                    },
                    {
                        "Id": "2",
                        "MessageId": "message-2",
                        "MD5OfMessageBody": "49f68a5c8493ec2c0bf489821c21fc3b",
                    },
                ],
                "Failed": [],
            },
            2,
            None,
            None,
            id="batch",
        ),
        pytest.param(
            "send_message_batch",
            {"Entries": _BATCH_ENTRIES},
            {
                "Successful": [
                    {
                        "Id": "1",
                        "MessageId": "message-1",
                        "MD5OfMessageBody": "49f68a5c8493ec2c0bf489821c21fc3b",
                    }
                ],
                "Failed": [{"Id": "2", "Code": "InternalError", "SenderFault": False}],
            },
            2,
            None,
            "InternalError",
            id="batch-shared-error-code",
        ),
        pytest.param(
            "send_message_batch",
            {"Entries": _BATCH_ENTRIES},
            {
                "Successful": [],
                "Failed": [
                    {"Id": "1", "Code": "InternalError", "SenderFault": False},
                    {"Id": "2", "Code": "InvalidParameterValue", "SenderFault": True},
                ],
            },
            2,
            None,
            "aws_batch_partial_failure",
            id="batch-mixed-error-codes",
        ),
    ],
)
def test_send_message(
    client_factory,
    capture_items,
    method,
    params,
    response,
    message_count,
    message_id,
    error_type,
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

    (span,) = spans[OP.QUEUE_PUBLISH]
    assert span["name"] == "send orders"
    assert span["status"] == ("ok" if error_type is None else "error")
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "producer"
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "send"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "send"
    assert attributes.get(SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT) == message_count
    assert attributes.get(SPANDATA.MESSAGING_MESSAGE_ID) == message_id
    assert attributes.get(SPANDATA.ERROR_TYPE) == error_type


@pytest.mark.parametrize(
    "response,message_count,message_id",
    [
        pytest.param({}, 0, None, id="empty"),
        pytest.param(
            {"Messages": [{"MessageId": "1", "ReceiptHandle": "receipt-handle-1"}]},
            1,
            "1",
            id="single-message",
        ),
        pytest.param(
            {
                "Messages": [
                    {"MessageId": "1", "ReceiptHandle": "receipt-handle-1"},
                    {"MessageId": "2", "ReceiptHandle": "receipt-handle-2"},
                ]
            },
            2,
            None,
            id="messages",
        ),
    ],
)
def test_receive_message(
    client_factory, capture_items, response, message_count, message_id
):
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
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "receive"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "receive"
    assert attributes[SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT] == message_count
    assert attributes.get(SPANDATA.MESSAGING_MESSAGE_ID) == message_id
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


@pytest.mark.parametrize(
    "names,sent_names",
    [
        pytest.param(None, ["sentry-trace", "baggage"], id="default"),
        pytest.param(
            ["orderId", "baggage"],
            ["orderId", "baggage", "sentry-trace"],
            id="caller-names",
        ),
        pytest.param(
            ["baggage", "sentry-trace"],
            ["baggage", "sentry-trace"],
            id="already-requested",
        ),
        pytest.param(["All"], ["All"], id="all"),
        pytest.param([".*"], [".*"], id="all-pattern"),
    ],
)
def test_receive_message_requests_carrier(
    client_factory, capture_items, names, sent_names
):
    client = client_factory("sqs")
    queue_url = "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"
    params = {} if names is None else {"MessageAttributeNames": names}
    caller_params = copy.deepcopy(params)

    with Stubber(client) as stubber:
        stubber.add_response(
            "receive_message",
            {},
            {"QueueUrl": queue_url, "MessageAttributeNames": sent_names},
        )
        capture_spans_by_op(
            lambda: client.receive_message(QueueUrl=queue_url, **params),
            capture_items,
        )

    assert params == caller_params


def test_receive_message_links(client_factory, capture_items):
    client = client_factory("sqs")
    sentry_trace = {
        "sentry-trace": {
            "DataType": "String",
            "StringValue": "771a43a4192642f0b136d5159a501700-a3e9e5ba6f99f4d6-1",
        }
    }
    sns_envelope = {
        "Type": "Notification",
        "MessageId": "sns-message-3",
        "TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
        "Message": "hi",
        "MessageAttributes": {
            "sentry-trace": {
                "Type": "String",
                "Value": "b2c1b1d6cb5f4a0d8a6a2f1c3e9d7b40-9c4f3a2b1e0d8f7a",
            }
        },
    }
    response = {
        "Messages": [
            # batch entries of one send share a context.
            {"MessageId": "1", "Body": "hi", "MessageAttributes": sentry_trace},
            {"MessageId": "2", "Body": "hi", "MessageAttributes": sentry_trace},
            # SNS-to-SQS delivery without raw message delivery.
            {"MessageId": "3", "Body": json.dumps(sns_envelope)},
            # trace id with malformed sentry-trace.
            {
                "MessageId": "4",
                "Body": "hi",
                "MessageAttributes": {
                    "sentry-trace": {
                        "DataType": "String",
                        "StringValue": "771a43a4192642f0b136d5159a501700",
                    }
                },
            },
            # JSON body that is not an SNS envelope.
            {"MessageId": "5", "Body": '{"sentry-trace": "not an SNS envelope"}'},
            {"MessageId": "6", "Body": "hi"},
        ]
    }

    with Stubber(client) as stubber:
        stubber.add_response("receive_message", response)
        spans = capture_spans_by_op(
            lambda: client.receive_message(
                QueueUrl="https://sqs.eu-north-1.amazonaws.com/123456789012/orders"
            ),
            capture_items,
        )

    (span,) = spans[OP.QUEUE_RECEIVE]
    link_type = {
        SPANDATA.SENTRY_LINK_TYPE: {"value": "message_origin", "type": "string"}
    }
    assert span["links"] == [
        # messages 1 and 2 share a context, so one link.
        {
            "trace_id": "771a43a4192642f0b136d5159a501700",
            "span_id": "a3e9e5ba6f99f4d6",
            "sampled": True,
            "attributes": link_type,
        },
        {
            "trace_id": "b2c1b1d6cb5f4a0d8a6a2f1c3e9d7b40",
            "span_id": "9c4f3a2b1e0d8f7a",
            "attributes": link_type,
        },
    ]
