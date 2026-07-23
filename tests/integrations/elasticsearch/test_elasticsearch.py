"""
Tests need a local Elasticsearch instance running, this can best be done using
```sh
docker run -d -p 9200:9200 --name elasticsearch-test --rm \
    -e discovery.type=single-node -e xpack.security.enabled=false \
    docker.elastic.co/elasticsearch/elasticsearch:9.1.4
```
"""

import json
import os
import uuid

import elasticsearch
import pytest
from elasticsearch import Elasticsearch

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.elasticsearch import ElasticsearchIntegration
from tests.conftest import ApproxDict

ES_HOST = os.environ.get("SENTRY_PYTHON_TEST_ES_HOST", "localhost")
ES_URL = "http://{}:9200".format(ES_HOST)

# The client only passes `endpoint_id` and `path_parts` to `perform_request`
# since 8.12. On older versions spans are named after the HTTP request instead.
HAS_ENDPOINT_METADATA = elasticsearch.__version__ >= (8, 12, 0)


def expected_span_names(index):
    if HAS_ENDPOINT_METADATA:
        return ["index", "indices.refresh", "search"]

    return [
        "PUT /{}/_doc/1".format(index),
        "POST /{}/_refresh".format(index),
        "POST /{}/_search".format(index),
    ]


@pytest.fixture
def index():
    client = Elasticsearch(ES_URL)
    index = "sentry-test-{}".format(uuid.uuid4().hex[:8])
    client.indices.create(index=index)
    yield index
    client.options(ignore_status=404).indices.delete(index=index)


def test_breadcrumbs(sentry_init, capture_events, index) -> None:
    sentry_init(integrations=[ElasticsearchIntegration()])
    events = capture_events()

    client = Elasticsearch(ES_URL)
    client.index(index=index, id="1", document={"title": "hello"})
    client.indices.refresh(index=index)
    res = client.search(index=index, query={"match": {"title": "hello"}})
    assert res["hits"]["total"]["value"] == 1

    capture_message("hi")

    (event,) = events
    crumbs = [
        crumb
        for crumb in event["breadcrumbs"]["values"]
        if crumb["category"] == "query"
    ]

    assert [crumb["message"] for crumb in crumbs] == expected_span_names(index)
    for crumb in crumbs:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.DB_SYSTEM: "elasticsearch",
                SPANDATA.SERVER_ADDRESS: ES_HOST,
                SPANDATA.SERVER_PORT: 9200,
            }
        )

    if HAS_ENDPOINT_METADATA:
        assert crumbs[2]["data"][SPANDATA.DB_OPERATION] == "search"
        assert crumbs[2]["data"]["db.elasticsearch.path_parts.index"] == index


def test_transaction_spans(sentry_init, capture_events, index) -> None:
    sentry_init(integrations=[ElasticsearchIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Elasticsearch(ES_URL)

    with start_transaction(name="test_transaction"):
        client.index(index=index, id="1", document={"title": "hello"})
        client.indices.refresh(index=index)
        client.search(index=index, query={"match": {"title": "hello"}})

    (event,) = events
    spans = [span for span in event["spans"] if span["op"] == "db"]

    assert [span["description"] for span in spans] == expected_span_names(index)
    for span in spans:
        assert span["op"] == "db"
        assert span["origin"] == "auto.db.elasticsearch"
        assert span["data"][SPANDATA.DB_SYSTEM] == "elasticsearch"
        assert span["data"][SPANDATA.SERVER_ADDRESS] == ES_HOST
        assert span["data"][SPANDATA.SERVER_PORT] == 9200

    search_span = spans[2]
    assert search_span["data"]["url.path"] == "/{}/_search".format(index)
    assert search_span["data"]["http.request.method"] == "POST"
    if HAS_ENDPOINT_METADATA:
        assert search_span["data"][SPANDATA.DB_OPERATION] == "search"
        assert search_span["data"]["db.elasticsearch.path_parts.index"] == index


def test_no_query_body_without_pii(sentry_init, capture_events, index) -> None:
    sentry_init(integrations=[ElasticsearchIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Elasticsearch(ES_URL)

    with start_transaction(name="test_transaction"):
        client.search(index=index, query={"match": {"title": "hello"}})

    (event,) = events
    (span,) = [span for span in event["spans"] if span["op"] == "db"]

    assert SPANDATA.DB_QUERY_TEXT not in span["data"]


def test_query_body_with_pii(sentry_init, capture_events, index) -> None:
    sentry_init(
        integrations=[ElasticsearchIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    client = Elasticsearch(ES_URL)

    with start_transaction(name="test_transaction"):
        client.search(index=index, query={"match": {"title": "hello"}})

    (event,) = events
    (span,) = [span for span in event["spans"] if span["op"] == "db"]

    assert span["data"][SPANDATA.DB_QUERY_TEXT] == {
        "query": {"match": {"title": "hello"}}
    }


def test_errors_do_not_go_unnoticed(sentry_init, capture_events, index) -> None:
    from elasticsearch import NotFoundError

    sentry_init(integrations=[ElasticsearchIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Elasticsearch(ES_URL)

    with start_transaction(name="test_transaction"):
        with pytest.raises(NotFoundError):
            client.get(index="does-not-exist-{}".format(index), id="1")

    (event,) = events
    (span,) = [span for span in event["spans"] if span["op"] == "db"]

    if HAS_ENDPOINT_METADATA:
        assert span["description"] == "get"
    else:
        assert span["description"] == "GET /does-not-exist-{}/_doc/1".format(index)
    assert span["status"] == "internal_error"


def test_client_untouched_when_integration_disabled(sentry_init, index) -> None:
    sentry_init(integrations=[], auto_enabling_integrations=False)

    client = Elasticsearch(ES_URL)
    client.index(index=index, id="1", document={"title": "hello"})
    client.indices.refresh(index=index)

    res = client.search(index=index, query={"match": {"title": "hello"}})
    assert res["hits"]["total"]["value"] == 1


def test_span_origin(sentry_init, capture_events, index) -> None:
    sentry_init(integrations=[ElasticsearchIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = Elasticsearch(ES_URL)

    with start_transaction(name="test_transaction"):
        client.indices.refresh(index=index)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    for span in event["spans"]:
        if span["op"] == "db":
            assert span["origin"] == "auto.db.elasticsearch"


@pytest.mark.asyncio
async def test_async_breadcrumbs(sentry_init, capture_events, index) -> None:
    AsyncElasticsearch = pytest.importorskip("elasticsearch").AsyncElasticsearch
    pytest.importorskip("aiohttp")

    sentry_init(integrations=[ElasticsearchIntegration()])
    events = capture_events()

    client = AsyncElasticsearch(ES_URL)
    try:
        await client.index(index=index, id="1", document={"title": "hello"})
        await client.indices.refresh(index=index)
        res = await client.search(index=index, query={"match": {"title": "hello"}})
        assert res["hits"]["total"]["value"] == 1
    finally:
        await client.close()

    capture_message("hi")

    (event,) = events
    crumbs = [
        crumb
        for crumb in event["breadcrumbs"]["values"]
        if crumb["category"] == "query"
    ]

    assert [crumb["message"] for crumb in crumbs] == expected_span_names(index)
    for crumb in crumbs:
        assert crumb["data"] == ApproxDict(
            {
                SPANDATA.DB_SYSTEM: "elasticsearch",
                SPANDATA.SERVER_ADDRESS: ES_HOST,
                SPANDATA.SERVER_PORT: 9200,
            }
        )


@pytest.mark.asyncio
async def test_async_transaction_spans(sentry_init, capture_events, index) -> None:
    AsyncElasticsearch = pytest.importorskip("elasticsearch").AsyncElasticsearch
    pytest.importorskip("aiohttp")

    sentry_init(integrations=[ElasticsearchIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    client = AsyncElasticsearch(ES_URL)
    try:
        with start_transaction(name="test_transaction"):
            await client.search(index=index, query={"match": {"title": "hello"}})
    finally:
        await client.close()

    (event,) = events
    (span,) = [span for span in event["spans"] if span["op"] == "db"]

    if HAS_ENDPOINT_METADATA:
        assert span["description"] == "search"
        assert span["data"][SPANDATA.DB_OPERATION] == "search"
    else:
        assert span["description"] == "POST /{}/_search".format(index)
    assert span["origin"] == "auto.db.elasticsearch"
    assert span["data"][SPANDATA.DB_SYSTEM] == "elasticsearch"


@pytest.mark.parametrize("with_pii", [True, False])
def test_span_streaming(sentry_init, capture_items, index, with_pii) -> None:
    import sentry_sdk

    sentry_init(
        integrations=[ElasticsearchIntegration()],
        _experiments={"trace_lifecycle": "stream"},
        traces_sample_rate=1.0,
        send_default_pii=with_pii,
    )
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent") as parent:
        trace_id = parent.trace_id
        span_id = parent.span_id

        client = Elasticsearch(ES_URL)
        client.search(index=index, query={"match": {"title": "hello"}})

    sentry_sdk.flush()

    (span,) = [
        item.payload
        for item in items
        if item.payload["attributes"].get(SPANDATA.DB_SYSTEM_NAME) == "elasticsearch"
    ]

    if HAS_ENDPOINT_METADATA:
        assert span["name"] == "search"
        assert span["attributes"][SPANDATA.DB_OPERATION_NAME] == "search"
        assert span["attributes"]["db.elasticsearch.path_parts.index"] == index
    else:
        assert span["name"] == "POST /{}/_search".format(index)

    assert span["trace_id"] == trace_id
    assert span["parent_span_id"] == span_id
    assert span["attributes"]["sentry.op"] == "db"
    assert span["attributes"]["sentry.origin"] == "auto.db.elasticsearch"
    assert span["attributes"]["url.path"] == "/{}/_search".format(index)
    assert span["attributes"]["http.request.method"] == "POST"
    assert span["attributes"][SPANDATA.SERVER_ADDRESS] == ES_HOST
    assert span["attributes"][SPANDATA.SERVER_PORT] == 9200

    if with_pii:
        assert json.loads(span["attributes"][SPANDATA.DB_QUERY_TEXT]) == {
            "query": {"match": {"title": "hello"}}
        }
    else:
        assert SPANDATA.DB_QUERY_TEXT not in span["attributes"]
