from unittest import mock

import pytest
import rediscluster

from sentry_sdk import capture_message
from sentry_sdk.api import start_span
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration
from tests.conftest import ApproxDict


MOCK_CONNECTION_POOL = mock.MagicMock()
MOCK_CONNECTION_POOL.connection_kwargs = {
    "host": "localhost",
    "port": 63791,
    "db": 1,
}


rediscluster_classes = [rediscluster.RedisCluster]

if hasattr(rediscluster, "StrictRedisCluster"):
    rediscluster_classes.append(rediscluster.StrictRedisCluster)


@pytest.fixture(autouse=True)
def monkeypatch_rediscluster_classes(reset_integrations):
    try:
        pipeline_cls = rediscluster.pipeline.ClusterPipeline
    except AttributeError:
        pipeline_cls = rediscluster.StrictClusterPipeline
    rediscluster.RedisCluster.pipeline = lambda *_, **__: pipeline_cls(
        connection_pool=MOCK_CONNECTION_POOL
    )
    pipeline_cls.execute = lambda *_, **__: None
    for cls in rediscluster_classes:
        cls.execute_command = lambda *_, **__: None


@pytest.mark.parametrize("rediscluster_cls", rediscluster_classes)
def test_rediscluster_basic(rediscluster_cls, sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    rc = rediscluster_cls(connection_pool=MOCK_CONNECTION_POOL)
    rc.get("foobar")
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
    "send_default_pii, expected_first_ten",
    [
        (False, ["GET 'foo'", "SET 'bar' [Filtered]", "SET 'baz' [Filtered]"]),
        (True, ["GET 'foo'", "SET 'bar' 1", "SET 'baz' 2"]),
    ],
)
def test_rediscluster_pipeline(
    sentry_init, capture_events, send_default_pii, expected_first_ten
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    rc = rediscluster.RedisCluster(connection_pool=MOCK_CONNECTION_POOL)
    with start_span(name="redis"):
        pipeline = rc.pipeline()
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "db.redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"]["redis.commands.count"] == 3
    assert span["data"]["redis.commands.first_ten"] == expected_first_ten
    assert span["data"] == ApproxDict(
        {
            SPANDATA.DB_SYSTEM: "redis",
            SPANDATA.DB_NAME: "1",
            SPANDATA.SERVER_ADDRESS: "localhost",
            SPANDATA.SERVER_PORT: 63791,
        }
    )
    assert span["tags"] == {
        "redis.transaction": "False",  # For Cluster, this is always False
        "redis.is_cluster": "True",
    }


@pytest.mark.parametrize("rediscluster_cls", rediscluster_classes)
def test_db_connection_attributes_client(sentry_init, capture_events, rediscluster_cls):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
    )
    events = capture_events()

    rc = rediscluster_cls(connection_pool=MOCK_CONNECTION_POOL)
    with start_span(name="redis"):
        rc.get("foobar")

    (event,) = events
    (span,) = event["spans"]

    assert span["data"] == ApproxDict(
        {
            SPANDATA.DB_SYSTEM: "redis",
            SPANDATA.DB_NAME: "1",
            SPANDATA.SERVER_ADDRESS: "localhost",
            SPANDATA.SERVER_PORT: 63791,
        }
    )


@pytest.mark.parametrize("rediscluster_cls", rediscluster_classes)
def test_db_connection_attributes_pipeline(
    sentry_init, capture_events, rediscluster_cls
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
    )
    events = capture_events()

    rc = rediscluster.RedisCluster(connection_pool=MOCK_CONNECTION_POOL)
    with start_span(name="redis"):
        pipeline = rc.pipeline()
        pipeline.get("foo")
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "db.redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"]["redis.commands.count"] == 1
    assert span["data"]["redis.commands.first_ten"] == ["GET 'foo'"]

    assert span["data"] == ApproxDict(
        {
            SPANDATA.DB_SYSTEM: "redis",
            SPANDATA.DB_NAME: "1",
            SPANDATA.SERVER_ADDRESS: "localhost",
            SPANDATA.SERVER_PORT: 63791,
        }
    )
