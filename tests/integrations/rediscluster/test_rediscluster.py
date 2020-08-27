import pytest
from sentry_sdk import capture_message
from sentry_sdk.integrations.redis import RedisIntegration

import rediscluster

rediscluster_classes = [rediscluster.RedisCluster]

if hasattr(rediscluster, "StrictRedisCluster"):
    rediscluster_classes.append(rediscluster.StrictRedisCluster)


@pytest.fixture(scope="module", autouse=True)
def monkeypatch_rediscluster_classes():
    for cls in rediscluster_classes:
        cls.execute_command = lambda *_, **__: None


@pytest.mark.parametrize("rediscluster_cls", rediscluster_classes)
def test_rediscluster_basic(rediscluster_cls, sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    rc = rediscluster_cls(connection_pool=True)
    rc.get("foobar")
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
