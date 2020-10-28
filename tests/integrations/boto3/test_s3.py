from sentry_sdk import Hub
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.integrations.boto3.aws_mock import MockResponse
from tests.integrations.boto3 import read_fixture

import boto3

session = boto3.Session(
    aws_access_key_id="-",
    aws_secret_access_key="-",
)


def test_basic(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with Hub.current.start_transaction() as transaction, MockResponse(
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
    assert span["op"] == "aws.request"
    assert span["description"] == "aws.s3.ListObjects"


def test_streaming(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with Hub.current.start_transaction() as transaction, MockResponse(
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
    assert span1["op"] == "aws.request"
    assert span1["description"] == "aws.s3.GetObject"
    span2 = event["spans"][1]
    assert span2["op"] == "aws.request.stream"
    assert span2["description"] == "aws.s3.GetObject"
    assert span2["parent_span_id"] == span1["span_id"]


def test_streaming_close(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, integrations=[Boto3Integration()])
    events = capture_events()

    s3 = session.resource("s3")
    with Hub.current.start_transaction() as transaction, MockResponse(
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
    assert span1["op"] == "aws.request"
    span2 = event["spans"][1]
    assert span2["op"] == "aws.request.stream"
