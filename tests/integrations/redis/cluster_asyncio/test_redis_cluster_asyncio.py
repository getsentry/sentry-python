import pytest

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration
from tests.conftest import ApproxDict

from redis.asyncio import cluster


async def fake_initialize(*_, **__):
    return None


async def fake_execute_command(*_, **__):
    return []


async def fake_execute(*_, **__):
    return None


@pytest.fixture(autouse=True)
def monkeypatch_rediscluster_asyncio_class(reset_integrations):
    pipeline_cls = cluster.ClusterPipeline
    cluster.NodesManager.initialize = fake_initialize
    cluster.RedisCluster.get_default_node = lambda *_, **__: cluster.ClusterNode(
        "localhost", 6379
    )
    cluster.RedisCluster.pipeline = lambda self, *_, **__: pipeline_cls(self)
    pipeline_cls.execute = fake_execute
    cluster.RedisCluster.execute_command = fake_execute_command


@pytest.mark.asyncio
async def test_async_breadcrumb(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    connection = cluster.RedisCluster(host="localhost", port=6379)

    await connection.get("foobar")
    capture_message("hi")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb == {
        "category": "redis",
        "message": "GET 'foobar'",
        "data": ApproxDict(
            {
                "db.operation": "GET",
                "redis.key": "foobar",
                "redis.command": "GET",
                "redis.is_cluster": True,
            }
        ),
        "timestamp": crumb["timestamp"],
        "type": "redis",
    }


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, description",
    [
        (False, "SET 'bar' [Filtered]"),
        (True, "SET 'bar' 1"),
    ],
)
@pytest.mark.asyncio
async def test_async_basic(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    description,
    span_streaming,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = cluster.RedisCluster(host="localhost", port=6379)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            await connection.set("bar", 1)
        sentry_sdk.flush()

        assert len(items) == 2
        redis_span, parent_span = items[0].payload, items[1].payload

        assert parent_span["name"] == "custom parent"
        assert redis_span["name"] == description
        attrs = redis_span["attributes"]
        assert attrs["sentry.op"] == "db.redis"
        assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
        assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
        assert attrs[SPANDATA.SERVER_ADDRESS] == "127.0.0.1"
        assert attrs[SPANDATA.SERVER_PORT] == 6379
        assert attrs[SPANDATA.DB_OPERATION_NAME] == "SET"
        assert attrs["db.redis.key"] == "bar"
    else:
        events = capture_events()
        with start_transaction():
            await connection.set("bar", 1)

        (event,) = events
        (span,) = event["spans"]
        assert span["op"] == "db.redis"
        assert span["description"] == description
        assert span["data"] == ApproxDict(
            {
                SPANDATA.DB_SYSTEM: "redis",
                # ClusterNode converts localhost to 127.0.0.1
                SPANDATA.SERVER_ADDRESS: "127.0.0.1",
                SPANDATA.SERVER_PORT: 6379,
            }
        )
        assert span["tags"] == {
            "redis.is_cluster": True,
            "db.operation": "SET",
            "redis.command": "SET",
            "redis.key": "bar",
        }


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, expected_first_ten",
    [
        (False, ["GET 'foo'", "SET 'bar' [Filtered]", "SET 'baz' [Filtered]"]),
        (True, ["GET 'foo'", "SET 'bar' 1", "SET 'baz' 2"]),
    ],
)
@pytest.mark.asyncio
async def test_async_redis_pipeline(
    sentry_init,
    capture_events,
    capture_items,
    send_default_pii,
    expected_first_ten,
    span_streaming,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = cluster.RedisCluster(host="localhost", port=6379)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            pipeline = connection.pipeline()
            pipeline.get("foo")
            pipeline.set("bar", 1)
            pipeline.set("baz", 2)
            await pipeline.execute()
        sentry_sdk.flush()

        assert len(items) == 2
        pipeline_span, parent_span = items[0].payload, items[1].payload

        assert parent_span["name"] == "custom parent"
        assert pipeline_span["name"] == "redis.pipeline.execute"
        attrs = pipeline_span["attributes"]
        assert attrs["sentry.op"] == "db.redis"
        assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
        assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
        assert attrs[SPANDATA.SERVER_ADDRESS] == "127.0.0.1"
        assert attrs[SPANDATA.SERVER_PORT] == 6379
    else:
        events = capture_events()
        with start_transaction():
            pipeline = connection.pipeline()
            pipeline.get("foo")
            pipeline.set("bar", 1)
            pipeline.set("baz", 2)
            await pipeline.execute()

        (event,) = events
        (span,) = event["spans"]
        assert span["op"] == "db.redis"
        assert span["description"] == "redis.pipeline.execute"
        assert span["data"] == ApproxDict(
            {
                "redis.commands": {
                    "count": 3,
                    "first_ten": expected_first_ten,
                },
                SPANDATA.DB_SYSTEM: "redis",
                # ClusterNode converts localhost to 127.0.0.1
                SPANDATA.SERVER_ADDRESS: "127.0.0.1",
                SPANDATA.SERVER_PORT: 6379,
            }
        )
        assert span["tags"] == {
            "redis.transaction": False,
            "redis.is_cluster": True,
        }


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio
async def test_async_span_origin(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    connection = cluster.RedisCluster(host="localhost", port=6379)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            # default case
            await connection.set("somekey", "somevalue")

            # pipeline
            pipeline = connection.pipeline(transaction=False)
            pipeline.get("somekey")
            pipeline.set("anotherkey", 1)
            await pipeline.execute()
        sentry_sdk.flush()

        assert len(items) == 3
        set_span, pipeline_span, parent_span = [item.payload for item in items]

        assert parent_span["name"] == "custom parent"
        assert parent_span["attributes"]["sentry.origin"] == "manual"
        assert set_span["attributes"]["sentry.origin"] == "auto.db.redis"
        assert pipeline_span["attributes"]["sentry.origin"] == "auto.db.redis"
    else:
        events = capture_events()
        with start_transaction(name="custom_transaction"):
            # default case
            await connection.set("somekey", "somevalue")

            # pipeline
            pipeline = connection.pipeline(transaction=False)
            pipeline.get("somekey")
            pipeline.set("anotherkey", 1)
            await pipeline.execute()

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"

        for span in event["spans"]:
            assert span["origin"] == "auto.db.redis"
