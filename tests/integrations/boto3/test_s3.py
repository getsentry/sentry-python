from unittest import mock

import boto3
import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.boto3 import Boto3Integration
from tests.conftest import ApproxDict
from tests.integrations.boto3 import read_fixture
from tests.integrations.boto3.aws_mock import MockResponse

session = boto3.Session(
    aws_access_key_id="-",
    aws_secret_access_key="-",
)


def test_basic(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        # disabled because session.resource() or s3.Bucket() result in a subprocess span for a
        # shell that runs "uname -p 2> /dev/null" on Python 3.7 with boto3 version 1.12.49.
        default_integrations=False,
        trace_lifecycle="stream",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent") as span, MockResponse(
        s3.meta.client, 200, {}, read_fixture("s3_list.xml")
    ):
        objects = [obj for obj in bucket.objects.all()]
        assert len(objects) == 2
        assert objects[0].key == "foo.txt"
        assert objects[1].key == "bar.txt"
        span.end()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 2
    span = spans[0]
    assert span["attributes"]["sentry.op"] == "http.client"
    assert span["name"] == "aws.s3.ListObjects"


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_streaming(
    sentry_init,
    capture_items,
    send_default_pii,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    s3 = session.resource("s3")
    obj = s3.Bucket("bucket").Object("foo.pdf")
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent") as span, MockResponse(
        s3.meta.client, 200, {}, b"hello"
    ):
        body = obj.get()["Body"]
        assert body.read(1) == b"h"
        assert body.read(2) == b"el"
        assert body.read(3) == b"lo"
        assert body.read(1) == b""
        span.end()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 3

    span1 = spans[0]
    assert span1["attributes"]["sentry.op"] == "http.client"
    assert span1["name"] == "aws.s3.GetObject"

    expected_attrs = {
        "http.request.method": "GET",
        "rpc.method": "S3/GetObject",
        "sentry.environment": "production",
        "sentry.op": "http.client",
        "sentry.origin": "auto.http.boto3",
        "sentry.release": mock.ANY,
        "sentry.sdk.name": "sentry.python",
        "sentry.sdk.version": mock.ANY,
        "sentry.segment.id": mock.ANY,
        "sentry.segment.name": "custom parent",
        "server.address": mock.ANY,
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
    }
    if send_default_pii:
        expected_attrs["url.full"] = "https://bucket.s3.amazonaws.com/foo.pdf"
        expected_attrs["url.fragment"] = ""
        expected_attrs["url.query"] = ""
    assert span1["attributes"] == ApproxDict(expected_attrs)

    if not send_default_pii:
        assert "url.full" not in span1["attributes"]
        assert "url.fragment" not in span1["attributes"]
        assert "url.query" not in span1["attributes"]

    span2 = spans[1]
    assert span2["attributes"]["sentry.op"] == "http.client.stream"
    assert span2["name"] == "aws.s3.GetObject"
    assert span2["parent_span_id"] == span1["span_id"]


def test_streaming_close(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream",
    )

    s3 = session.resource("s3")
    obj = s3.Bucket("bucket").Object("foo.pdf")
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent") as span, MockResponse(
        s3.meta.client, 200, {}, b"hello"
    ):
        body = obj.get()["Body"]
        assert body.read(1) == b"h"
        body.close()  # close partially-read stream
        span.end()

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    assert len(spans) == 3
    span1 = spans[0]
    assert span1["attributes"]["sentry.op"] == "http.client"
    span2 = spans[1]
    assert span2["attributes"]["sentry.op"] == "http.client.stream"


@pytest.mark.tests_internal_exceptions
def test_omit_url_data_if_parsing_fails(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")
    items = capture_items("span")

    with mock.patch(
        "sentry_sdk.integrations.boto3.parse_url",
        side_effect=ValueError,
    ):
        with sentry_sdk.traces.start_span(name="custom parent") as span, MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
            objects = [obj for obj in bucket.objects.all()]
            assert len(objects) == 2
            assert objects[0].key == "foo.txt"
            assert objects[1].key == "bar.txt"
            span.end()

            sentry_sdk.flush()
            spans = [item.payload for item in items]
            assert spans[0]["attributes"] == ApproxDict(
                {
                    "http.request.method": "GET",
                    "rpc.method": "S3/ListObjects",
                    "sentry.environment": "production",
                    "sentry.op": "http.client",
                    "sentry.origin": "auto.http.boto3",
                    "sentry.release": mock.ANY,
                    "sentry.sdk.name": "sentry.python",
                    "sentry.sdk.version": mock.ANY,
                    "sentry.segment.id": mock.ANY,
                    "sentry.segment.name": "custom parent",
                    "server.address": mock.ANY,
                    "thread.id": mock.ANY,
                    "thread.name": mock.ANY,
                }
            )

    assert "url.full" not in spans[0]["attributes"]
    assert "url.fragment" not in spans[0]["attributes"]
    assert "url.query" not in spans[0]["attributes"]


def test_span_origin(
    sentry_init,
    capture_items,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"), MockResponse(
        s3.meta.client, 200, {}, read_fixture("s3_list.xml")
    ):
        _ = [obj for obj in bucket.objects.all()]

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    assert spans[1]["attributes"]["sentry.origin"] == "manual"
    assert spans[0]["attributes"]["sentry.origin"] == "auto.http.boto3"


def test_breadcrumb(sentry_init, capture_events):
    sentry_init(
        integrations=[Boto3Integration()],
        default_integrations=False,
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")

    events = capture_events()

    with MockResponse(s3.meta.client, 200, {}, read_fixture("s3_list.xml")):
        _ = [obj for obj in bucket.objects.all()]

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"
    assert crumb["data"] == ApproxDict(
        {
            "aws.request.url": mock.ANY,
            SPANDATA.HTTP_METHOD: "GET",
            SPANDATA.HTTP_QUERY: mock.ANY,
            SPANDATA.HTTP_FRAGMENT: "",
        }
    )


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_breadcrumb_span_streaming(sentry_init, capture_events, send_default_pii):
    sentry_init(
        integrations=[Boto3Integration()],
        default_integrations=False,
        trace_lifecycle="stream",
        send_default_pii=send_default_pii,
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")

    events = capture_events()

    with sentry_sdk.traces.start_span(name="custom parent"), MockResponse(
        s3.meta.client, 200, {}, read_fixture("s3_list.xml")
    ):
        _ = [obj for obj in bucket.objects.all()]

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"
    assert crumb["category"] == "httplib"

    if send_default_pii:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.URL_FULL: mock.ANY,
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
                SPANDATA.URL_QUERY: mock.ANY,
                SPANDATA.URL_FRAGMENT: "",
            }
        )
    else:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
            }
        )
        assert SPANDATA.URL_FULL not in crumb["data"]
        assert SPANDATA.URL_QUERY not in crumb["data"]
        assert SPANDATA.URL_FRAGMENT not in crumb["data"]
