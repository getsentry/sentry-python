import pytest

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import ATTRS
from sentry_sdk.integrations.redis import RedisIntegration
from tests.conftest import ApproxDict

from fakeredis.aioredis import FakeRedis


@pytest.mark.asyncio
async def test_async_basic(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    connection = FakeRedis()

    await connection.get("foobar")
    capture_message("hi")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb == {
        "category": "redis",
        "message": "GET 'foobar'",
        "data": {
            "db.operation": "GET",
            "redis.key": "foobar",
            "redis.command": "GET",
            "redis.is_cluster": False,
        },
        "timestamp": crumb["timestamp"],
        "type": "redis",
    }


@pytest.mark.parametrize(
    "is_transaction, send_default_pii, expected_first_ten",
    [
        (False, False, ["GET 'foo'", "SET 'bar' [Filtered]", "SET 'baz' [Filtered]"]),
        (True, True, ["GET 'foo'", "SET 'bar' 1", "SET 'baz' 2"]),
    ],
)
@pytest.mark.asyncio
async def test_async_redis_pipeline(
    sentry_init, capture_events, is_transaction, send_default_pii, expected_first_ten
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    connection = FakeRedis()
    with start_transaction():
        pipeline = connection.pipeline(transaction=is_transaction)
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
            ATTRS.DB_NAME: "0",
            ATTRS.SERVER_ADDRESS: connection.connection_pool.connection_kwargs.get(
                "host"
            ),
            ATTRS.SERVER_PORT: 6379,
        }
    )
    assert span["tags"] == {
        "redis.transaction": is_transaction,
        "redis.is_cluster": False,
    }


@pytest.mark.asyncio
async def test_async_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeRedis()
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
