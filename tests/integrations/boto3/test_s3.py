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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_basic(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        # disabled because session.resource() or s3.Bucket() result in a subprocess span for a
        # shell that runs "uname -p 2> /dev/null" on Python 3.7 with boto3 version 1.12.49.
        default_integrations=False,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")

    if span_streaming:
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
        assert span["name"] == "S3.ListObjects"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction() as transaction, MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
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
        assert span["description"] == "S3.ListObjects"


@pytest.mark.parametrize("send_default_pii", [True, False])
@pytest.mark.parametrize("span_streaming", [True, False])
def test_streaming(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
    send_default_pii,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    s3 = session.resource("s3")
    obj = s3.Bucket("bucket").Object("foo.pdf")

    if span_streaming:
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
        assert span1["name"] == "S3.GetObject"

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
        assert span1["attributes"] == ApproxDict(expected_attrs)

        assert "url.fragment" not in span1["attributes"]
        assert "url.query" not in span1["attributes"]
        if not send_default_pii:
            assert "url.full" not in span1["attributes"]

        span2 = spans[1]
        assert span2["attributes"]["sentry.op"] == "http.client.stream"
        assert span2["name"] == "S3.GetObject"
        assert span2["parent_span_id"] == span1["span_id"]
    else:
        events = capture_events()

        with sentry_sdk.start_transaction() as transaction, MockResponse(
            s3.meta.client, 200, {}, b"hello"
        ):
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
        assert span1["description"] == "S3.GetObject"
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
        assert span2["description"] == "S3.GetObject"
        assert span2["parent_span_id"] == span1["span_id"]


@pytest.mark.parametrize("span_streaming", [True, False])
def test_streaming_close(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream" if span_streaming else "static",
    )

    s3 = session.resource("s3")
    obj = s3.Bucket("bucket").Object("foo.pdf")

    if span_streaming:
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
    else:
        events = capture_events()

        with sentry_sdk.start_transaction() as transaction, MockResponse(
            s3.meta.client, 200, {}, b"hello"
        ):
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
@pytest.mark.parametrize("span_streaming", [True, False])
def test_omit_url_data_if_parsing_fails(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        send_default_pii=True,
        trace_lifecycle="stream" if span_streaming else "static",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")

    if span_streaming:
        items = capture_items("span")

        with mock.patch(
            "sentry_sdk.integrations.boto3.parse_url",
            side_effect=ValueError,
        ):
            with sentry_sdk.traces.start_span(
                name="custom parent"
            ) as span, MockResponse(
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
    else:
        events = capture_events()

        with mock.patch(
            "sentry_sdk.integrations.boto3.parse_url",
            side_effect=ValueError,
        ):
            with sentry_sdk.start_transaction() as transaction, MockResponse(
                s3.meta.client, 200, {}, read_fixture("s3_list.xml")
            ):
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


@pytest.mark.parametrize("span_streaming", [True, False])
def test_span_origin(
    sentry_init,
    capture_events,
    capture_items,
    span_streaming,
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        trace_lifecycle="stream" if span_streaming else "static",
    )

    s3 = session.resource("s3")
    bucket = s3.Bucket("bucket")

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"), MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
            _ = [obj for obj in bucket.objects.all()]

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[1]["attributes"]["sentry.origin"] == "manual"
        assert spans[0]["attributes"]["sentry.origin"] == "auto.http.boto3"
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(), MockResponse(
            s3.meta.client, 200, {}, read_fixture("s3_list.xml")
        ):
            _ = [obj for obj in bucket.objects.all()]

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"
        assert event["spans"][0]["origin"] == "auto.http.boto3"


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
            }
        )
        assert SPANDATA.URL_FRAGMENT not in crumb["data"]
    else:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.HTTP_REQUEST_METHOD: "GET",
            }
        )
        assert SPANDATA.URL_FULL not in crumb["data"]
        assert SPANDATA.URL_QUERY not in crumb["data"]
        assert SPANDATA.URL_FRAGMENT not in crumb["data"]


BUCKET_URL = "https://bucket.s3.amazonaws.com/"

# ``expected_query`` of ``None`` means no URL data is recorded at all; ``""``
# means the URL is recorded without a query string.
# Structure of the parameters is "init_kwargs, expected_query"
URL_QUERY_PARAMS = [
    pytest.param(
        {"send_default_pii": True},
        "list-type=2&prefix=foo&continuation-token=abc&encoding-type=url",
        id="send_default_pii_true",
    ),
    pytest.param(
        {"send_default_pii": False},
        None,
        id="send_default_pii_false",
    ),
    pytest.param(
        {},
        None,
        id="defaults",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {}}},
        "list-type=2&prefix=foo&continuation-token=%5BFiltered%5D&encoding-type=url",
        id="data_collection_denylist_default",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {"mode": "denylist", "terms": ["prefix"]}
                }
            }
        },
        "list-type=2&prefix=%5BFiltered%5D&continuation-token=%5BFiltered%5D&encoding-type=url",
        id="data_collection_denylist_custom_terms",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {"mode": "allowlist", "terms": ["prefix"]}
                }
            }
        },
        "list-type=%5BFiltered%5D&prefix=foo&continuation-token=%5BFiltered%5D&encoding-type=%5BFiltered%5D",
        id="data_collection_allowlist",
    ),
    pytest.param(
        {
            "_experiments": {
                "data_collection": {
                    "url_query_params": {
                        "mode": "allowlist",
                        "terms": ["continuation-token"],
                    }
                }
            }
        },
        "list-type=%5BFiltered%5D&prefix=%5BFiltered%5D&continuation-token=%5BFiltered%5D&encoding-type=%5BFiltered%5D",
        id="data_collection_allowlist_sensitive_term",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"url_query_params": {"mode": "off"}}}},
        "",
        id="data_collection_off",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"url_query_params": {"mode": "off"}}},
        },
        "",
        id="data_collection_wins_over_send_default_pii",
    ),
]


@pytest.mark.parametrize("init_kwargs, expected_query", URL_QUERY_PARAMS)
def test_url_query_data_collection_span_streaming(
    sentry_init, capture_items, init_kwargs, expected_query
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[Boto3Integration()],
        default_integrations=False,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    client = session.client("s3")

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"), MockResponse(
        client, 200, {}, read_fixture("s3_list.xml")
    ):
        client.list_objects_v2(Bucket="bucket", Prefix="foo", ContinuationToken="abc")

    sentry_sdk.flush()

    (span,) = [
        item.payload
        for item in items
        if item.payload["attributes"].get("sentry.op") == "http.client"
    ]

    if expected_query is None:
        assert SPANDATA.URL_QUERY not in span["attributes"]
        assert SPANDATA.URL_FULL not in span["attributes"]
    elif expected_query == "":
        assert SPANDATA.URL_QUERY not in span["attributes"]
        assert span["attributes"][SPANDATA.URL_FULL] == BUCKET_URL
    else:
        assert span["attributes"][SPANDATA.URL_QUERY] == expected_query
        assert (
            span["attributes"][SPANDATA.URL_FULL] == BUCKET_URL + "?" + expected_query
        )


@pytest.mark.parametrize("init_kwargs, expected_query", URL_QUERY_PARAMS)
def test_url_query_data_collection_breadcrumb(
    sentry_init, capture_events, init_kwargs, expected_query
):
    sentry_init(
        integrations=[Boto3Integration()],
        default_integrations=False,
        trace_lifecycle="stream",
        **init_kwargs,
    )

    client = session.client("s3")

    events = capture_events()

    with sentry_sdk.traces.start_span(name="custom parent"), MockResponse(
        client, 200, {}, read_fixture("s3_list.xml")
    ):
        client.list_objects_v2(Bucket="bucket", Prefix="foo", ContinuationToken="abc")

    capture_message("Testing!")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    if expected_query is None:
        assert SPANDATA.URL_QUERY not in crumb["data"]
        assert SPANDATA.URL_FULL not in crumb["data"]
    elif expected_query == "":
        assert SPANDATA.URL_QUERY not in crumb["data"]
        assert crumb["data"][SPANDATA.URL_FULL] == BUCKET_URL
    else:
        assert crumb["data"][SPANDATA.URL_QUERY] == expected_query
        assert crumb["data"][SPANDATA.URL_FULL] == BUCKET_URL + "?" + expected_query
