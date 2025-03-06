from unittest import mock

import boto3
import pytest

import sentry_sdk
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.conftest import ApproxDict
from tests.integrations.boto3 import read_fixture
from tests.integrations.boto3.aws_mock import MockResponse


session = boto3.Session(
    aws_access_key_id="-",
    aws_secret_access_key="-",
)


def test_basic(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with sentry_sdk.start_span() as transaction, MockResponse(
        s3.meta.client, 200, {}, read_fixture("s3_list.xml")
    ):
        bucket = s3.Bucket("bucket")
        items = [obj for obj in bucket.objects.all()]
        assert len(items) == 2
        assert items[0].key == "foo.txt"
        assert items[1].key == "bar.txt"
        transaction.finish()

    (event,) = events
    assert event["type"] == "transaction"
    assert len(event["spans"]) == 1
    (span,) = event["spans"]
    assert span["op"] == "http.client"
    assert span["description"] == "aws.s3.ListObjects"


def test_breadcrumb(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    try:
        s3 = session.resource("s3")
        with sentry_sdk.start_span(), MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
            bucket = s3.Bucket("bucket")
            # read bucket (this makes http request)
            [obj for obj in bucket.objects.all()]
            1 / 0
    except Exception as e:
        sentry_sdk.capture_exception(e)

    (_, event) = events
    crumb = event["breadcrumbs"]["values"][0]
    assert crumb == {
        "type": "http",
        "category": "httplib",
        "data": {
            "http.method": "GET",
            "aws.request.url": "https://bucket.s3.amazonaws.com/",
            "http.query": "encoding-type=url",
            "http.fragment": "",
        },
        "timestamp": mock.ANY,
    }


def test_streaming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with sentry_sdk.start_span() as transaction, MockResponse(
        s3.meta.client, 200, {}, b"hello"
    ):
        obj = s3.Bucket("bucket").Object("foo.pdf")
        body = obj.get()["Body"]
        assert body.read(1) == b"h"
        assert body.read(2) == b"el"
        assert body.read(3) == b"lo"
        assert body.read(1) == b""
        transaction.finish()

    (event,) = events
    assert event["type"] == "transaction"
    assert len(event["spans"]) == 2

    span1 = event["spans"][0]
    assert span1["op"] == "http.client"
    assert span1["description"] == "aws.s3.GetObject"
    assert span1["data"] == ApproxDict(
        {
            "http.method": "GET",
            "aws.request.url": "https://bucket.s3.amazonaws.com/foo.pdf",
            "http.fragment": "",
            "http.query": "",
        }
    )

    span2 = event["spans"][1]
    assert span2["op"] == "http.client.stream"
    assert span2["description"] == "aws.s3.GetObject"
    assert span2["parent_span_id"] == span1["span_id"]


def test_streaming_close(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with sentry_sdk.start_span() as transaction, MockResponse(
        s3.meta.client, 200, {}, b"hello"
    ):
        obj = s3.Bucket("bucket").Object("foo.pdf")
        body = obj.get()["Body"]
        assert body.read(1) == b"h"
        body.close()  # close partially-read stream
        transaction.finish()

    (event,) = events
    assert event["type"] == "transaction"
    assert len(event["spans"]) == 2
    span1 = event["spans"][0]
    assert span1["op"] == "http.client"
    span2 = event["spans"][1]
    assert span2["op"] == "http.client.stream"


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")

    with mock.patch(
        "sentry_sdk.integrations.boto3.parse_url",
        side_effect=ValueError,
    ):
        with sentry_sdk.start_span() as transaction, MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
            bucket = s3.Bucket("bucket")
            items = [obj for obj in bucket.objects.all()]
            assert len(items) == 2
            assert items[0].key == "foo.txt"
            assert items[1].key == "bar.txt"
            transaction.finish()

    (event,) = events
    assert event["spans"][0]["data"] == ApproxDict(
        {
            "http.method": "GET",
            # no url data
        }
    )

    assert "aws.request.url" not in event["spans"][0]["data"]
    assert "http.fragment" not in event["spans"][0]["data"]
    assert "http.query" not in event["spans"][0]["data"]


def test_span_origin(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with sentry_sdk.start_span(), MockResponse(
        s3.meta.client, 200, {}, read_fixture("s3_list.xml")
    ):
        bucket = s3.Bucket("bucket")
        _ = [obj for obj in bucket.objects.all()]

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.http.boto3"
