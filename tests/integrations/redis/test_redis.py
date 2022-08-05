from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.redis import RedisIntegration

from fakeredis import FakeStrictRedis


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    connection = FakeStrictRedis()

    connection.get("foobar")
    capture_message("hi")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb == {
        "category": "redis",
        "message": "GET 'foobar'",
        "data": {"redis.key": "foobar", "redis.command": "GET"},
        "timestamp": crumb["timestamp"],
        "type": "redis",
    }


def test_redis_pipeline(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        pipeline = connection.pipeline()
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"] == {
        "commands": {"count": 3, "first_ten": ["GET foo", "SET bar 1", "SET baz 2"]}
    }
