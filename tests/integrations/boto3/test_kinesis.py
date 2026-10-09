from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import capture_spans_by_op
from tests.integrations.boto3.helpers import client_factory as client_factory


def test_request_attributes(client_factory, capture_items):
    client = client_factory("kinesis")
    stream_arn = "arn:aws:kinesis:eu-north-1:123456789012:stream/orders"

    with Stubber(client) as stubber:
        stubber.add_response(
            "list_stream_consumers", {"Consumers": []}, {"StreamARN": stream_arn}
        )
        spans = capture_spans_by_op(
            lambda: client.list_stream_consumers(**{"StreamARN": stream_arn}),
            capture_items,
        )

    (span,) = spans[OP.HTTP_CLIENT]
    attributes = span["attributes"]
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == stream_arn
