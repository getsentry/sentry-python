import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.integrations.boto3.helpers import capture_spans_by_op
from tests.integrations.boto3.helpers import client_factory as client_factory
from tests.integrations.utils import DATA_COLLECTION_USER_INFO_CASES


def test_request_attributes(client_factory, capture_items):
    client = client_factory("sns")
    topic_arn = "arn:aws:sns:eu-north-1:123456789012:orders"

    with Stubber(client) as stubber:
        stubber.add_response("delete_topic", {}, {"TopicArn": topic_arn})
        spans = capture_spans_by_op(
            lambda: client.delete_topic(**{"TopicArn": topic_arn}), capture_items
        )

    (span,) = spans[OP.RPC]
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == topic_arn


ENDPOINT_ARN = "arn:aws:sns:eu-north-1:123456789012:endpoint/GCM/myapp/5f1c8a3e-0b7d-3c55-9b5e-1a2b3c4d5e6f"


@pytest.mark.parametrize(
    "method,params,response,user_identifying",
    [
        # `EndpointARN` can be a device token.
        pytest.param(
            "get_endpoint_attributes",
            {"EndpointArn": ENDPOINT_ARN},
            {"Attributes": {}},
            True,
            id="endpoint_arn",
        ),
        # `SubscriptionArn` can be a subscriber email/phone number.
        pytest.param(
            "unsubscribe",
            {
                "SubscriptionArn": "arn:aws:sns:eu-north-1:123456789012:orders:8e2a6b1c-4d3f-4f6a-9c1e-2b3d4e5f6a7b"
            },
            {},
            True,
            id="subscription_arn",
        ),
        # `TargetArn` can be an endpoint ARN.
        pytest.param(
            "publish",
            {"TargetArn": ENDPOINT_ARN, "Message": "hi"},
            {"MessageId": "1"},
            True,
            id="target_arn_endpoint",
        ),
        # `TargetArn` can be a topic ARN, which is not gated.
        pytest.param(
            "publish",
            {
                "TargetArn": "arn:aws:sns:eu-north-1:123456789012:orders",
                "Message": "hi",
            },
            {"MessageId": "1"},
            False,
            id="target_arn_topic",
        ),
    ],
)
@pytest.mark.parametrize(
    "data_collection,expect_user_info", DATA_COLLECTION_USER_INFO_CASES
)
def test_resource_id_span_attributes_data_collection(
    sentry_init,
    client_factory,
    capture_items,
    method,
    params,
    response,
    user_identifying,
    data_collection,
    expect_user_info,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        data_collection=data_collection,
    )
    client = client_factory("sns")
    (arn,) = (value for key, value in params.items() if key.endswith("Arn"))

    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.HTTP_CLIENT]
    attributes = span["attributes"]
    if expect_user_info or not user_identifying:
        assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == arn
        assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    else:
        assert SPANDATA.CLOUD_RESOURCE_ID not in attributes
        assert SPANDATA.CLOUD_ACCOUNT_ID not in attributes
