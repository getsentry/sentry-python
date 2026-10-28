import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES

_BATCH_ENTRIES = [
    {"Id": "1", "Message": "hi"},
    {"Id": "2", "Message": "hi"},
]


@pytest.mark.parametrize(
    "method,params,response,topic_arn",
    [
        pytest.param(
            "delete_topic",
            {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts"},
            {},
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            id="topic-arn",
        ),
        pytest.param(
            "tag_resource",
            {
                "ResourceArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
                "Tags": [{"Key": "team", "Value": "ops"}],
            },
            {},
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            id="resource-arn",
        ),
        pytest.param(
            "create_topic",
            {"Name": "alerts"},
            {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts"},
            None,
            id="create-topic",
        ),
    ],
)
def test_request_attributes(
    client_factory, capture_items, method, params, response, topic_arn
):
    client = client_factory("sns")

    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.RPC]
    attributes = span["attributes"]
    assert attributes[SPANDATA.MESSAGING_DESTINATION_NAME] == "alerts"
    assert attributes.get(SPANDATA.AWS_SNS_TOPIC_ARN) == topic_arn
    assert attributes.get(SPANDATA.CLOUD_RESOURCE_ID) == topic_arn
    if topic_arn is not None:
        assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert SPANDATA.MESSAGING_SYSTEM not in attributes


@pytest.mark.parametrize(
    "method,params,response,destination,topic_arn,message_count,message_id,error_type",
    [
        pytest.param(
            "publish",
            {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts", "Message": "hi"},
            {"MessageId": "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b"},
            "alerts",
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            None,
            "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b",
            None,
            id="topic",
        ),
        pytest.param(
            "publish",
            {
                "TargetArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
                "Message": "hi",
            },
            {"MessageId": "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b"},
            "alerts",
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            None,
            "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b",
            None,
            id="target-topic",
        ),
        pytest.param(
            "publish",
            {"PhoneNumber": "+15555550123", "Message": "hi"},
            {"MessageId": "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b"},
            "[Filtered]",
            None,
            None,
            "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b",
            None,
            id="phone-number",
        ),
        pytest.param(
            "publish",
            {
                "TargetArn": "arn:aws:sns:eu-north-1:123456789012:endpoint/GCM/shop/5f1c8a3e-0b7d-3c55-9b5e-1a2b3c4d5e6f",
                "Message": "hi",
            },
            {"MessageId": "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b"},
            "[Filtered]",
            None,
            None,
            "4c1f2a7e-9b3d-5e6f-8a1b-2c3d4e5f6a7b",
            None,
            id="device-endpoint",
        ),
        pytest.param(
            "publish_batch",
            {
                "TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
                "PublishBatchRequestEntries": _BATCH_ENTRIES,
            },
            {
                "Successful": [
                    {"Id": "1", "MessageId": "message-1"},
                    {"Id": "2", "MessageId": "message-2"},
                ],
                "Failed": [],
            },
            "alerts",
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            2,
            None,
            None,
            id="batch",
        ),
        pytest.param(
            "publish_batch",
            {
                "TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
                "PublishBatchRequestEntries": _BATCH_ENTRIES,
            },
            {
                "Successful": [{"Id": "1", "MessageId": "message-1"}],
                "Failed": [{"Id": "2", "Code": "InternalError", "SenderFault": False}],
            },
            "alerts",
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            2,
            None,
            "InternalError",
            id="batch-shared-error-code",
        ),
        pytest.param(
            "publish_batch",
            {
                "TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts",
                "PublishBatchRequestEntries": _BATCH_ENTRIES,
            },
            {
                "Successful": [],
                "Failed": [
                    {"Id": "1", "Code": "InternalError", "SenderFault": False},
                    {"Id": "2", "Code": "EndpointDisabled", "SenderFault": True},
                ],
            },
            "alerts",
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            2,
            None,
            "aws_batch_partial_failure",
            id="batch-mixed-error-codes",
        ),
    ],
)
def test_publish(
    client_factory,
    capture_items,
    method,
    params,
    response,
    destination,
    topic_arn,
    message_count,
    message_id,
    error_type,
):
    client = client_factory("sns")

    require_botocore_model_fields(
        client,
        "publish_batch",
    )

    with Stubber(client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.QUEUE_PUBLISH]
    assert span["name"] == f"send {destination}"
    assert span["status"] == ("ok" if error_type is None else "error")
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_KIND] == "producer"
    assert attributes[SPANDATA.MESSAGING_OPERATION_TYPE] == "send"
    assert attributes[SPANDATA.MESSAGING_OPERATION_NAME] == "publish"
    assert attributes.get(SPANDATA.AWS_SNS_TOPIC_ARN) == topic_arn
    assert attributes.get(SPANDATA.MESSAGING_BATCH_MESSAGE_COUNT) == message_count
    assert attributes.get(SPANDATA.MESSAGING_MESSAGE_ID) == message_id
    assert attributes.get(SPANDATA.ERROR_TYPE) == error_type


def test_publish_without_destination(client_factory, capture_items):
    client = client_factory("sns")

    with Stubber(client) as stubber:
        stubber.add_client_error(
            "publish", service_error_code="InvalidParameter", http_status_code=400
        )

        def call():
            with pytest.raises(ClientError):
                client.publish(Message="hi")

        spans = capture_spans_by_op(call, capture_items)

    (span,) = spans[OP.QUEUE_PUBLISH]
    assert span["name"] == "send sns.eu-north-1.amazonaws.com:443"
    assert SPANDATA.MESSAGING_DESTINATION_NAME not in span["attributes"]


@pytest.mark.parametrize(
    "method,params,response,filtered_arn",
    [
        pytest.param(
            "get_endpoint_attributes",
            {
                "EndpointArn": "arn:aws:sns:eu-north-1:123456789012:endpoint/GCM/shop/5f1c8a3e-0b7d-3c55-9b5e-1a2b3c4d5e6f"
            },
            {"Attributes": {}},
            "arn:aws:sns:eu-north-1:123456789012:app/GCM/shop",
            id="endpoint",
        ),
        pytest.param(
            "unsubscribe",
            {
                "SubscriptionArn": "arn:aws:sns:eu-north-1:123456789012:alerts:8e2a6b1c-4d3f-4f6a-9c1e-2b3d4e5f6a7b"
            },
            {},
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            id="subscription",
        ),
        pytest.param(
            "publish",
            {
                "TargetArn": "arn:aws:sns:eu-north-1:123456789012:endpoint/GCM/shop/5f1c8a3e-0b7d-3c55-9b5e-1a2b3c4d5e6f",
                "Message": "hi",
            },
            {"MessageId": "4c1f2a7e"},
            "arn:aws:sns:eu-north-1:123456789012:app/GCM/shop",
            id="device-endpoint",
        ),
        pytest.param(
            "publish",
            {"TopicArn": "arn:aws:sns:eu-north-1:123456789012:alerts", "Message": "hi"},
            {"MessageId": "4c1f2a7e"},
            "arn:aws:sns:eu-north-1:123456789012:alerts",
            id="topic",
        ),
    ],
)
@pytest.mark.parametrize(
    "data_collection,expect_user_info", DATA_COLLECTION_USER_INFO_CASES
)
def test_resource_id_data_collection(
    sentry_init,
    client_factory,
    capture_items,
    method,
    params,
    response,
    filtered_arn,
    data_collection,
    expect_user_info,
):
    client = client_factory("sns")
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        data_collection=data_collection,
    )
    (arn,) = (value for key, value in params.items() if key.endswith("Arn"))

    with Stubber(client) as stubber:
        stubber.add_response(method, response)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    ((span,),) = spans.values()
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == (
        arn if expect_user_info else filtered_arn
    )
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
