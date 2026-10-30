import inspect

import pytest
import pytest_asyncio
from aiobotocore import __version__ as AIOBOTOCORE_VERSION
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from aiohttp import web
from botocore.exceptions import ClientError

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.aiobotocore import AioBotocoreIntegration
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.boto3.consts import AIOBOTOCORE_ORIGIN
from sentry_sdk.utils import parse_version

LIST_BUCKETS = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Owner><ID>owner</ID></Owner>
<Buckets><Bucket><Name>bucket</Name><CreationDate>2026-01-01T00:00:00.000Z</CreationDate></Bucket></Buckets>
</ListAllMyBucketsResult>"""
NO_SUCH_KEY = b"""<?xml version="1.0" encoding="UTF-8"?>
<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>"""
OBJECT_BODY = b"hello world"

# aiobotocore's bodies are botocore `StreamingBody`s since 3.8.
STREAMING_BODY_INSTRUMENTED = parse_version(AIOBOTOCORE_VERSION) >= (3, 8)

HTTP_SESSIONS = [pytest.param({}, id="aiohttp")]
if STREAMING_BODY_INSTRUMENTED:
    from aiobotocore.httpxsession import HttpxSession

    HTTP_SESSIONS.append(pytest.param({"http_session_cls": HttpxSession}, id="httpx"))


@pytest_asyncio.fixture
async def s3_server(aiohttp_server):
    requests = []

    async def list_buckets(request):
        requests.append(request)
        return web.Response(
            body=LIST_BUCKETS,
            content_type="application/xml",
            headers={"x-amz-request-id": "list-request-id"},
        )

    async def get_object(request):
        requests.append(request)
        if request.match_info["key"] == "missing":
            return web.Response(
                status=404, body=NO_SUCH_KEY, content_type="application/xml"
            )
        return web.Response(body=OBJECT_BODY)

    app = web.Application()
    app.router.add_get("/", list_buckets)
    app.router.add_get("/{bucket}/{key}", get_object)
    server = await aiohttp_server(app)
    server.requests = requests
    return server


def _s3_client(s3_server, **config):
    return get_session().create_client(
        "s3",
        region_name="eu-north-1",
        endpoint_url=str(s3_server.make_url("/")),
        aws_access_key_id="-",
        aws_secret_access_key="-",
        config=AioConfig(s3={"addressing_style": "path"}, **config),
    )


def _client_spans(items):
    return [
        item.payload
        for item in items
        if item.type == "span"
        and item.payload["attributes"].get(SPANDATA.SENTRY_ORIGIN) == AIOBOTOCORE_ORIGIN
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "integrations",
    [
        pytest.param([AioBotocoreIntegration()], id="aiobotocore"),
        # aiobotocore clients run boto3's patched `__init__` as well.
        pytest.param(
            [Boto3Integration(), AioBotocoreIntegration()], id="boto3-and-aiobotocore"
        ),
    ],
)
async def test_api_call(sentry_init, capture_items, s3_server, integrations):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=integrations,
        default_integrations=False,
    )
    items = capture_items("span", "event")

    async with _s3_client(s3_server) as client:
        with sentry_sdk.start_span(name="test") as parent:
            await client.list_buckets()
    capture_message("after the call")
    sentry_sdk.flush()

    (span,) = _client_spans(items)
    assert span["name"] == "S3.ListBuckets"
    assert span["parent_span_id"] == parent.span_id
    assert span["status"] == "ok"
    attributes = span["attributes"]
    assert attributes[SPANDATA.SENTRY_OP] == OP.RPC
    assert attributes[SPANDATA.RPC_METHOD] == "ListBuckets"
    assert attributes[SPANDATA.HTTP_REQUEST_METHOD] == "GET"
    assert attributes[SPANDATA.URL_FULL] == str(s3_server.make_url("/"))
    assert attributes[SPANDATA.HTTP_STATUS_CODE] == 200
    assert attributes[SPANDATA.AWS_REQUEST_ID] == "list-request-id"

    # the request hooks run once per request.
    (request,) = s3_server.requests
    assert request.headers.getall("sentry-trace") == [
        f"{span['trace_id']}-{span['span_id']}-1"
    ]
    (event,) = [item.payload for item in items if item.type == "event"]
    (crumb,) = event["breadcrumbs"]["values"]
    assert crumb["type"] == "http"


@pytest.mark.asyncio
async def test_error(sentry_init, capture_items, s3_server):
    sentry_init(traces_sample_rate=1.0, integrations=[AioBotocoreIntegration()])
    items = capture_items("span")

    async with _s3_client(s3_server) as client:
        with sentry_sdk.start_span(name="test"):
            with pytest.raises(ClientError):
                await client.get_object(Bucket="bucket", Key="missing")
    sentry_sdk.flush()

    (span,) = _client_spans(items)
    assert span["name"] == "S3.GetObject"
    assert span["status"] == "error"
    assert span["attributes"][SPANDATA.ERROR_TYPE] == "NoSuchKey"
    assert span["attributes"][SPANDATA.HTTP_STATUS_CODE] == 404


async def _read(body):
    assert await body.read() == OBJECT_BODY


async def _close(body):
    closed = body.close()
    # the httpx body's `close()` is a coroutine.
    if inspect.isawaitable(closed):
        await closed


async def _exit_context(body):
    async with body:
        pass


@pytest.mark.asyncio
@pytest.mark.skipif(
    not STREAMING_BODY_INSTRUMENTED, reason="aiobotocore<3.8 bodies aren't instrumented"
)
@pytest.mark.parametrize("http_session", HTTP_SESSIONS)
@pytest.mark.parametrize("consume", [_read, _close, _exit_context])
async def test_streaming_body(
    sentry_init, capture_items, s3_server, http_session, consume
):
    sentry_init(traces_sample_rate=1.0, integrations=[AioBotocoreIntegration()])
    items = capture_items("span")

    async with _s3_client(s3_server, **http_session) as client:
        with sentry_sdk.start_span(name="test"):
            response = await client.get_object(Bucket="bucket", Key="key")
            sentry_sdk.flush()
            # the client span ends with the body, not when the call returns.
            assert not _client_spans(items)
            await consume(response["Body"])
    sentry_sdk.flush()

    streaming_span, span = _client_spans(items)
    assert span["name"] == "S3.GetObject"
    assert streaming_span["parent_span_id"] == span["span_id"]
    assert streaming_span["attributes"][SPANDATA.SENTRY_OP] == OP.HTTP_CLIENT_STREAM
    assert span["end_timestamp"] >= streaming_span["end_timestamp"]
