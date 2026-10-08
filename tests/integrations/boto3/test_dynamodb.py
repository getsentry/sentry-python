import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import capture_spans_by_op
from tests.integrations.boto3.helpers import client_factory as client_factory


@pytest.mark.parametrize(
    "method,params,response,expected_resource_id",
    [
        pytest.param(
            "list_tags_of_resource",
            {"ResourceArn": ("arn:aws:dynamodb:eu-north-1:123456789012:table/orders")},
            {"Tags": []},
            "arn:aws:dynamodb:eu-north-1:123456789012:table/orders",
            id="arn",
        ),
        pytest.param(
            "delete_table",
            {"TableName": "orders"},
            {},
            None,
            id="name",
        ),
    ],
)
def test_request_attributes(
    client_factory, capture_items, method, params, response, expected_resource_id
):
    client = client_factory("dynamodb")

    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.HTTP_CLIENT]
    attributes = span["attributes"]
    if expected_resource_id is None:
        assert SPANDATA.CLOUD_ACCOUNT_ID not in attributes
        assert SPANDATA.CLOUD_RESOURCE_ID not in attributes
    else:
        assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
        assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == expected_resource_id
