import pytest
from fakeredis.aioredis import FakeRedis

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration


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
    sentry_init,
    capture_events,
    capture_items,
    is_transaction,
    send_default_pii,
    expected_first_ten,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        pipeline = connection.pipeline(transaction=is_transaction)
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
    assert attrs[SPANDATA.DB_NAMESPACE] == "0"
    assert attrs[SPANDATA.SERVER_ADDRESS] == (
        connection.connection_pool.connection_kwargs.get("host")
    )
    assert attrs[SPANDATA.SERVER_PORT] == 6379


@pytest.mark.asyncio
async def test_async_span_origin(sentry_init, capture_events, capture_items):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()

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
