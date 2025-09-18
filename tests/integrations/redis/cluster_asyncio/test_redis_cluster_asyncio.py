import pytest

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import ATTRS
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


@pytest.mark.parametrize(
    "send_default_pii, description",
    [
        (False, "SET 'bar' [Filtered]"),
        (True, "SET 'bar' 1"),
    ],
)
@pytest.mark.asyncio
async def test_async_basic(sentry_init, capture_events, send_default_pii, description):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    connection = cluster.RedisCluster(host="localhost", port=6379)
    with start_transaction():
        await connection.set("bar", 1)

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "db.redis"
    assert span["description"] == description
    assert span["data"] == ApproxDict(
        {
            ATTRS.DB_SYSTEM: "redis",
            # ClusterNode converts localhost to 127.0.0.1
            ATTRS.SERVER_ADDRESS: "127.0.0.1",
            ATTRS.SERVER_PORT: 6379,
        }
    )
    assert span["tags"] == {
        "redis.is_cluster": True,
        "db.operation": "SET",
        "redis.command": "SET",
        "redis.key": "bar",
    }


@pytest.mark.parametrize(
    "send_default_pii, expected_first_ten",
    [
        (False, ["GET 'foo'", "SET 'bar' [Filtered]", "SET 'baz' [Filtered]"]),
        (True, ["GET 'foo'", "SET 'bar' 1", "SET 'baz' 2"]),
    ],
)
@pytest.mark.asyncio
async def test_async_redis_pipeline(
    sentry_init, capture_events, send_default_pii, expected_first_ten
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    connection = cluster.RedisCluster(host="localhost", port=6379)
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
            ATTRS.DB_SYSTEM: "redis",
            # ClusterNode converts localhost to 127.0.0.1
            ATTRS.SERVER_ADDRESS: "127.0.0.1",
            ATTRS.SERVER_PORT: 6379,
        }
    )
    assert span["tags"] == {
        "redis.transaction": False,
        "redis.is_cluster": True,
    }


@pytest.mark.asyncio
async def test_async_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = cluster.RedisCluster(host="localhost", port=6379)
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
