import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory


@pytest.mark.parametrize("source", ["arn", "queue-url"])
def test_request_attributes(client_factory, capture_items, source):
    client = client_factory("sqs")

    if source == "arn":
        require_botocore_model_fields(client, "start_message_move_task")
        resource_id = "arn:aws:sqs:eu-north-1:123456789012:orders-dlq"
        method = "start_message_move_task"
        params = {"SourceArn": resource_id}
        response = {"TaskHandle": "task-handle"}
    else:
        resource_id = None
        method = "delete_queue"
        params = {
            "QueueUrl": "https://sqs.eu-north-1.amazonaws.com/123456789012/orders"
        }
        response = {}

    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )

    (span,) = spans[OP.HTTP_CLIENT]
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    if resource_id is None:
        assert SPANDATA.CLOUD_RESOURCE_ID not in attributes
    else:
        assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == resource_id
