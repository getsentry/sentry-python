from unittest import mock

import pytest
import rediscluster

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration

MOCK_CONNECTION_POOL = mock.MagicMock()
MOCK_CONNECTION_POOL.connection_kwargs = {
    "host": "localhost",
    "port": 63791,
    "db": 1,
}


@pytest.fixture(autouse=True)
def monkeypatch_rediscluster_classes(reset_integrations):
    pipeline_cls = rediscluster.pipeline.ClusterPipeline
    rediscluster.RedisCluster.pipeline = lambda *_, **__: pipeline_cls(
        connection_pool=MOCK_CONNECTION_POOL
    )
    pipeline_cls.execute = lambda *_, **__: None
    rediscluster.RedisCluster.execute_command = lambda *_, **__: None


def test_rediscluster_breadcrumb(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    rc = rediscluster.RedisCluster(connection_pool=MOCK_CONNECTION_POOL)
    rc.get("foobar")
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
            "redis.is_cluster": True,
        },
        "timestamp": crumb["timestamp"],
        "type": "redis",
    }


@pytest.mark.parametrize(
    "data_collection, description",
    [
        ({"database_query_data": False}, "SET 'bar' [Filtered]"),
        ({"database_query_data": True}, "SET 'bar' 1"),
    ],
)
def test_rediscluster_basic(sentry_init, capture_items, data_collection, description):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        data_collection=data_collection,
    )
    items = capture_items("span")

    rc = rediscluster.RedisCluster(connection_pool=MOCK_CONNECTION_POOL)
    with sentry_sdk.start_span(name="custom parent"):
        rc.set("bar", 1)
    sentry_sdk.flush()

    span, parent_span = [item.payload for item in items]
    assert parent_span["name"] == "custom parent"
    assert span["name"] == description

    attrs = span["attributes"]
    assert attrs["sentry.op"] == OP.DB_QUERY
    assert attrs["sentry.origin"] == "auto.db.redis"
    assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
    assert attrs[SPANDATA.DB_NAMESPACE] == "1"
    assert attrs[SPANDATA.SERVER_ADDRESS] == "localhost"
    assert attrs[SPANDATA.SERVER_PORT] == 63791
    assert attrs[SPANDATA.DB_OPERATION_NAME] == "SET"
    assert attrs["db.redis.key"] == "bar"


def test_rediscluster_pipeline(sentry_init, capture_items):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
    )
    items = capture_items("span")

    rc = rediscluster.RedisCluster(connection_pool=MOCK_CONNECTION_POOL)
    with sentry_sdk.start_span(name="custom parent"):
        pipeline = rc.pipeline()
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()
    sentry_sdk.flush()

    span, parent_span = [item.payload for item in items]
    assert parent_span["name"] == "custom parent"
    assert span["name"] == "redis.pipeline.execute"

    attrs = span["attributes"]
    assert attrs["sentry.op"] == OP.DB_QUERY
    assert attrs["sentry.origin"] == "auto.db.redis"
    assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
    assert attrs[SPANDATA.DB_NAMESPACE] == "1"
    assert attrs[SPANDATA.SERVER_ADDRESS] == "localhost"
    assert attrs[SPANDATA.SERVER_PORT] == 63791
