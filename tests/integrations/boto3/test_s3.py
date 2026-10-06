import json
from copy import deepcopy

import pytest
from botocore.stub import Stubber

from sentry_sdk.consts import OP, SPANDATA
from tests.integrations.boto3.helpers import (
    assert_client_span,
    capture_spans_by_op,
    require_botocore_model_fields,
)


def _assert_s3_attributes(span, expected):
    # Compare the whole S3-specific set, including the absence of attributes
    # such as file.size on a ranged response or part_number on HeadObject.
    actual = {
        key: value
        for key, value in span["attributes"].items()
        if key.startswith("aws.s3.")
        or key in (SPANDATA.FILE_SIZE, SPANDATA.HTTP_BODY_SIZE)
    }
    assert actual == expected


def _stubbed_span(client, capture_items, method, params, response):
    with Stubber(client) as stubber:
        stubber.add_response(method, response, expected_params=params)
        spans = capture_spans_by_op(
            lambda: getattr(client, method)(**params), capture_items
        )
        stubber.assert_no_pending_responses()
    (span,) = spans[OP.HTTP_CLIENT]
    assert_client_span(
        span,
        "S3",
        client.meta.method_to_api_mapping[method],
        server_address="s3.eu-north-1.amazonaws.com",
    )
    return span


def test_request_attributes(s3_client, capture_items):
    params = {
        "Bucket": "bucket",
        "Key": "file.txt",
        "UploadId": "upload-id",
        "PartNumber": 1,
        "Body": b"private-content",
    }
    original = deepcopy(params)

    span = _stubbed_span(s3_client, capture_items, "upload_part", params, {})

    _assert_s3_attributes(
        span,
        {
            SPANDATA.AWS_S3_BUCKET: "bucket",
            SPANDATA.AWS_S3_KEY: "file.txt",
            SPANDATA.AWS_S3_UPLOAD_ID: "upload-id",
            SPANDATA.AWS_S3_PART_NUMBER: 1,
        },
    )
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

    _assert_s3_attributes(span, expected_attributes)
    assert params["CopySource"] is source
    assert source == copy_source


def test_delete_serialization(s3_client, capture_items):
    delete = {"Quiet": True, "Objects": [{"VersionId": "version-1", "Key": "file.txt"}]}
    params = {"Bucket": "bucket", "Delete": deepcopy(delete)}
    original = deepcopy(params)
    caller_delete = params["Delete"]
    caller_objects = caller_delete["Objects"]

    span = _stubbed_span(s3_client, capture_items, "delete_objects", params, {})

    _assert_s3_attributes(
        span,
        {
            SPANDATA.AWS_S3_BUCKET: "bucket",
            SPANDATA.AWS_S3_DELETE: '{"Objects":[{"Key":"file.txt","VersionId":"version-1"}],"Quiet":true}',
        },
    )
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

    _assert_s3_attributes(
        span,
        {SPANDATA.AWS_S3_BUCKET: "bucket", SPANDATA.AWS_S3_KEY: "file.txt", **expected},
    )
