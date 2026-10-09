from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import capture_spans_by_op
from tests.integrations.boto3.helpers import client_factory as client_factory


def test_request_attributes(client_factory, capture_items):
    client = client_factory("lambda")
    function_arn = "arn:aws:lambda:eu-north-1:123456789012:function:orders"

    with Stubber(client) as stubber:
        stubber.add_response("delete_function", {}, {"FunctionName": function_arn})
        spans = capture_spans_by_op(
            lambda: client.delete_function(**{"FunctionName": function_arn}),
            capture_items,
        )

    (span,) = spans[OP.HTTP_CLIENT]
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == function_arn
