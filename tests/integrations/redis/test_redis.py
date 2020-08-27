from sentry_sdk import capture_message
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
