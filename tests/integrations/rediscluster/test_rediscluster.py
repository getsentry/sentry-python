import pytest
from sentry_sdk import capture_message
from sentry_sdk.api import start_transaction
from sentry_sdk.integrations.redis import RedisIntegration

import rediscluster

rediscluster_classes = [rediscluster.RedisCluster]

if hasattr(rediscluster, "StrictRedisCluster"):
    rediscluster_classes.append(rediscluster.StrictRedisCluster)


@pytest.fixture(scope="module", autouse=True)
def monkeypatch_rediscluster_classes():
    # FIXME: patch StrictRedisCluster
    rediscluster.RedisCluster.pipeline = lambda *_, **__: rediscluster.ClusterPipeline(
        connection_pool=True
    )
    rediscluster.ClusterPipeline.execute = lambda *_, **__: None
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


def test_rediscluster_pipeline(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    rc = rediscluster.RedisCluster(connection_pool=True)
    with start_transaction():
        pipeline = rc.pipeline()
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"] == {
        "commands": {
            "count": 3,
            "first_ten": [
                [["GET", "foo"], {}],
                [["SET", "bar", 1], {}],
                [["SET", "baz", 2], {}],
            ],
        }
    }
