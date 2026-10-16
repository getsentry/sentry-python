import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import client_factory as client_factory

STREAM_ARN = "arn:aws:kinesis:eu-north-1:123456789012:stream/orders"
CONSUMER_ARN = STREAM_ARN + "/consumer/billing:1760000000"


def _stubbed_span(client_factory, capture_items, method, params, response):
    client = client_factory("kinesis")
    require_botocore_model_fields(
        client, method, input_fields=tuple(params), output_fields=tuple(response)
    )
    with Stubber(client) as stubber:
        stubber.add_response(method, response, params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
    (span,) = spans[OP.RPC]
    return span


@pytest.mark.parametrize(
    "method,params,response,expected",
    [
        pytest.param(
            "put_record",
            {"StreamName": "orders", "Data": b"{}", "PartitionKey": "customer-1"},
            {"ShardId": "shardId-000000000000", "SequenceNumber": "1"},
            {SPANDATA.AWS_KINESIS_STREAM_NAME: "orders"},
            id="stream-name",
        ),
        pytest.param(
            "list_stream_consumers",
            {"StreamARN": STREAM_ARN},
            {"Consumers": []},
            {
                SPANDATA.AWS_KINESIS_STREAM_NAME: "orders",
                SPANDATA.CLOUD_RESOURCE_ID: STREAM_ARN,
            },
            id="stream-arn",
        ),
        pytest.param(
            "deregister_stream_consumer",
            {"ConsumerARN": CONSUMER_ARN},
            {},
            {
                SPANDATA.AWS_KINESIS_STREAM_NAME: "orders",
                SPANDATA.CLOUD_RESOURCE_ID: CONSUMER_ARN,
            },
            id="consumer-arn",
        ),
        pytest.param(
            "list_tags_for_resource",
            {"ResourceARN": CONSUMER_ARN},
            {"Tags": []},
            {
                SPANDATA.AWS_KINESIS_STREAM_NAME: "orders",
                SPANDATA.CLOUD_RESOURCE_ID: CONSUMER_ARN,
            },
            id="resource-arn",
        ),
    ],
)
def test_stream_attributes(
    client_factory, capture_items, method, params, response, expected
):
    span = _stubbed_span(client_factory, capture_items, method, params, response)

    assert {
        key: value
        for key, value in span["attributes"].items()
        if key in (SPANDATA.AWS_KINESIS_STREAM_NAME, SPANDATA.CLOUD_RESOURCE_ID)
    } == expected


@pytest.mark.parametrize(
    "error_codes,expected_error_type",
    [
        pytest.param((), None, id="ok"),
        pytest.param(
            ("ProvisionedThroughputExceededException",) * 2,
            "ProvisionedThroughputExceededException",
            id="one-code",
        ),
        pytest.param(
            ("ProvisionedThroughputExceededException", "InternalFailure"),
            "aws_batch_partial_failure",
            id="different-codes",
        ),
    ],
)
def test_put_records_outcome(
    client_factory, capture_items, error_codes, expected_error_type
):
    params = {
        "StreamName": "orders",
        "Records": [
            {"Data": b"card 4242", "PartitionKey": "customer-1"},
            {"Data": b"card 4242", "PartitionKey": "customer-2"},
            {"Data": b"card 4242", "PartitionKey": "customer-3"},
        ],
    }
    response = {
        "Records": [{"ShardId": "shardId-000000000000", "SequenceNumber": "1"}]
        + [{"ErrorCode": code, "ErrorMessage": "failed"} for code in error_codes],
    }
    # the model's minimum is 1, so the stubber only accepts it for failed records.
    if error_codes:
        response["FailedRecordCount"] = len(error_codes)  # type: ignore
    span = _stubbed_span(client_factory, capture_items, "put_records", params, response)

    assert span["status"] == ("ok" if expected_error_type is None else "error")
    assert span["attributes"].get(SPANDATA.ERROR_TYPE) == expected_error_type
