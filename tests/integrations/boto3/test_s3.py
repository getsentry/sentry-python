import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3.consts import AWS_RPC_SYSTEM_NAME, ORIGIN
from tests.integrations.boto3.helpers import (
    capture_spans_by_op,
    require_botocore_model_fields,
)
from tests.integrations.boto3.helpers import (
    client_factory as client_factory,
)
from tests.integrations.boto3.helpers import (
    s3_client as s3_client,
)


def _stubbed_span(client, capture_items, method, params, response):
    with Stubber(client) as stubber:
        stubber.add_response(method, response, expected_params=params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
        stubber.assert_no_pending_responses()
    (span,) = spans[OP.HTTP_CLIENT]
    operation = client.meta.method_to_api_mapping[method]
    assert span["name"] == "S3.%s" % operation
    assert span.get("end_timestamp") is not None
    attributes = span.get("attributes", {})
    assert attributes[SPANDATA.SENTRY_OP] == OP.HTTP_CLIENT
    assert attributes[SPANDATA.SENTRY_ORIGIN] == ORIGIN
    assert attributes[SPANDATA.SENTRY_KIND] == "client"
    assert attributes[SPANDATA.CLOUD_PROVIDER] == "aws"
    assert attributes[SPANDATA.RPC_SYSTEM_NAME] == AWS_RPC_SYSTEM_NAME
    assert attributes[SPANDATA.RPC_SERVICE] == "S3"
    assert attributes[SPANDATA.RPC_METHOD] == operation
    assert attributes[SPANDATA.CLOUD_REGION] == "eu-north-1"
    assert attributes[SPANDATA.SERVER_ADDRESS] == "s3.eu-north-1.amazonaws.com"
    assert attributes[SPANDATA.SERVER_PORT] == 443
    return span


def test_request_attributes(s3_client, capture_items):
    bucket_arn = "arn:aws:s3:eu-north-1:123456789012:accesspoint/orders"
    params = {
        "Bucket": bucket_arn,
        "Key": "file.txt",
        "UploadId": "upload-id",
        "PartNumber": 1,
        "Body": b"private-content",
    }
    original = deepcopy(params)

    span = _stubbed_span(s3_client, capture_items, "upload_part", params, {})

    attributes = span.get("attributes", {})
    assert attributes[SPANDATA.CLOUD_ACCOUNT_ID] == "123456789012"
    assert attributes[SPANDATA.CLOUD_RESOURCE_ID] == bucket_arn
    assert attributes[SPANDATA.AWS_S3_BUCKET] == bucket_arn
    assert attributes[SPANDATA.AWS_S3_KEY] == "file.txt"
    assert attributes[SPANDATA.AWS_S3_UPLOAD_ID] == "upload-id"
    assert attributes[SPANDATA.AWS_S3_PART_NUMBER] == 1
    assert SPANDATA.AWS_S3_COPY_SOURCE not in attributes
    assert SPANDATA.AWS_S3_DELETE not in attributes
    assert SPANDATA.FILE_SIZE not in attributes
    assert SPANDATA.HTTP_BODY_SIZE not in attributes
    assert params == original
    assert "private-content" not in json.dumps(span)


@pytest.mark.parametrize(
    "method,copy_source,expected",
    [
        pytest.param(
            "copy_object",
            "source/path/file.txt",
            "source/path/file.txt",
            id="string",
        ),
        pytest.param(
            "copy_object",
            {"Bucket": "source", "Key": "path/file.txt"},
            "source/path/file.txt",
            id="dictionary",
        ),
        pytest.param(
            "upload_part_copy",
            {"Bucket": "source", "Key": "path/file.txt", "VersionId": "version-1"},
            "source/path/file.txt?versionId=version-1",
            id="versioned-dictionary",
        ),
    ],
)
def test_copy_source(s3_client, capture_items, method, copy_source, expected):
    source = deepcopy(copy_source)
    params = {"Bucket": "bucket", "Key": "file.txt", "CopySource": source}
    expected_attributes = {
        SPANDATA.AWS_S3_BUCKET: "bucket",
        SPANDATA.AWS_S3_KEY: "file.txt",
        SPANDATA.AWS_S3_COPY_SOURCE: expected,
    }
    if method == "upload_part_copy":
        params.update(UploadId="upload-id", PartNumber=1)
        expected_attributes.update(
            {SPANDATA.AWS_S3_UPLOAD_ID: "upload-id", SPANDATA.AWS_S3_PART_NUMBER: 1}
        )

    span = _stubbed_span(s3_client, capture_items, method, params, {})

    attributes = span.get("attributes", {})
    for key, value in expected_attributes.items():
        assert attributes[key] == value
    for key in (
        SPANDATA.AWS_S3_UPLOAD_ID,
        SPANDATA.AWS_S3_PART_NUMBER,
        SPANDATA.AWS_S3_DELETE,
        SPANDATA.FILE_SIZE,
        SPANDATA.HTTP_BODY_SIZE,
    ):
        if key not in expected_attributes:
            assert key not in attributes
    assert params["CopySource"] is source
    assert source == copy_source


@pytest.mark.parametrize(
    "delete, input_fields, expected_serialized_delete",
    [
        pytest.param(
            {"Quiet": True, "Objects": [{"VersionId": "version-1", "Key": "file.txt"}]},
            (),
            '{"Objects":[{"Key":"file.txt","VersionId":"version-1"}],"Quiet":true}',
            id="basic",
        ),
        pytest.param(
            {
                "Objects": [
                    {
                        "Key": "file.txt",
                        "VersionId": "version-1",
                        "ETag": "etag",
                        "LastModifiedTime": datetime(
                            2026, 10, 8, 12, 34, 56, tzinfo=timezone.utc
                        ),
                        "Size": 123,
                    }
                ]
            },
            ("Delete.Objects.LastModifiedTime",),
            '{"Objects":[{"ETag":"etag","Key":"file.txt","LastModifiedTime":"2026-10-08T12:34:56+00:00","Size":123,"VersionId":"version-1"}]}',
            id="last-modified-time",
        ),
    ],
)
def test_delete_serialization(
    s3_client, capture_items, delete, input_fields, expected_serialized_delete
):
    require_botocore_model_fields(
        s3_client,
        "delete_objects",
        input_fields=input_fields,
    )

    params = {"Bucket": "bucket", "Delete": deepcopy(delete)}
    original = deepcopy(params)
    caller_delete = params["Delete"]
    caller_objects = caller_delete["Objects"]

    span = _stubbed_span(s3_client, capture_items, "delete_objects", params, {})

    attributes = span.get("attributes", {})
    assert attributes[SPANDATA.AWS_S3_BUCKET] == "bucket"
    assert attributes[SPANDATA.AWS_S3_DELETE] == expected_serialized_delete
    assert SPANDATA.AWS_S3_KEY not in attributes
    assert SPANDATA.AWS_S3_UPLOAD_ID not in attributes
    assert SPANDATA.AWS_S3_COPY_SOURCE not in attributes
    assert SPANDATA.AWS_S3_PART_NUMBER not in attributes
    assert SPANDATA.FILE_SIZE not in attributes
    assert SPANDATA.HTTP_BODY_SIZE not in attributes
    assert params == original
    assert params["Delete"] is caller_delete
    assert caller_delete["Objects"] is caller_objects


@pytest.mark.parametrize(
    "method,extra_params,response,expected",
    [
        pytest.param(
            "head_object",
            {},
            {"ContentLength": 1024},
            {SPANDATA.FILE_SIZE: 1024},
            id="head-whole-object",
        ),
        pytest.param(
            "head_object",
            {},
            {"ContentLength": 0},
            {SPANDATA.FILE_SIZE: 0},
            id="head-empty-object",
        ),
        pytest.param(
            "head_object",
            {"Range": "bytes=0-3"},
            {"ContentLength": 4},
            {},
            id="head-range",
        ),
        pytest.param(
            "head_object", {"PartNumber": 1}, {"ContentLength": 4}, {}, id="head-part"
        ),
        pytest.param(
            "get_object",
            {"Range": "bytes=0-3"},
            {"ContentLength": 4},
            {SPANDATA.HTTP_BODY_SIZE: 4},
            id="get-range",
        ),
        pytest.param(
            "get_object_attributes",
            {"ObjectAttributes": ["ObjectSize"]},
            {"ObjectSize": 1024},
            {SPANDATA.FILE_SIZE: 1024},
            id="object-attributes-size",
        ),
        pytest.param(
            "put_object",
            {"Body": b"data", "WriteOffsetBytes": 1020},
            {"Size": 1024},
            {SPANDATA.FILE_SIZE: 1024},
            id="put-append-size",
        ),
        pytest.param("put_object", {"Body": b"data"}, {}, {}, id="put-missing-size"),
        pytest.param(
            "complete_multipart_upload",
            {"UploadId": "upload-id", "MpuObjectSize": 1024},
            {},
            {SPANDATA.FILE_SIZE: 1024, SPANDATA.AWS_S3_UPLOAD_ID: "upload-id"},
            id="multipart-request-size",
        ),
    ],
)
def test_size_attributes(
    s3_client, capture_items, method, extra_params, response, expected
):
    require_botocore_model_fields(
        s3_client,
        method,
        input_fields=tuple(
            field
            for field in ("MpuObjectSize", "WriteOffsetBytes")
            if field in extra_params
        ),
        output_fields=tuple(response),
    )
    params = {"Bucket": "bucket", "Key": "file.txt", **deepcopy(extra_params)}

    span = _stubbed_span(s3_client, capture_items, method, params, response)

    attributes = span.get("attributes", {})
    expected_attributes = {
        SPANDATA.AWS_S3_BUCKET: "bucket",
        SPANDATA.AWS_S3_KEY: "file.txt",
        **expected,
    }
    for key, value in expected_attributes.items():
        assert attributes[key] == value
    for key in (
        SPANDATA.AWS_S3_UPLOAD_ID,
        SPANDATA.AWS_S3_COPY_SOURCE,
        SPANDATA.AWS_S3_PART_NUMBER,
        SPANDATA.AWS_S3_DELETE,
        SPANDATA.FILE_SIZE,
        SPANDATA.HTTP_BODY_SIZE,
    ):
        if key not in expected_attributes:
            assert key not in attributes
