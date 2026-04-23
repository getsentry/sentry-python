import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.api import start_transaction
from sentry_sdk.integrations.redis import RedisIntegration
from tests.conftest import ApproxDict

import redis


@pytest.fixture(autouse=True)
def monkeypatch_rediscluster_class(reset_integrations):
    pipeline_cls = redis.cluster.ClusterPipeline
    redis.cluster.NodesManager.initialize = lambda *_, **__: None
    redis.RedisCluster.command = lambda *_: []
    redis.RedisCluster.pipeline = lambda *_, **__: pipeline_cls(None, None)
    redis.RedisCluster.get_default_node = lambda *_, **__: redis.cluster.ClusterNode(
        "localhost", 6379
    )
    pipeline_cls.execute = lambda *_, **__: None
    redis.RedisCluster.execute_command = lambda *_, **__: []


def test_rediscluster_breadcrumb(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    rc = redis.RedisCluster(host="localhost", port=6379)
    rc.get("foobar")
    capture_message("hi")

    (event,) = events
    crumbs = event["breadcrumbs"]["values"]

    # on initializing a RedisCluster, a COMMAND call is made - this is not important for the test
    # but must be accounted for
    assert len(crumbs) in (1, 2)
    assert len(crumbs) == 1 or crumbs[0]["message"] == "COMMAND"

    crumb = crumbs[-1]

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


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.parametrize(
    "send_default_pii, description",
    [
        (False, "SET 'bar' [Filtered]"),
        (True, "SET 'bar' 1"),
    ],
)
def test_rediscluster_basic(
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

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            rc = redis.RedisCluster(host="localhost", port=6379)
            rc.set("bar", 1)
        sentry_sdk.flush()

        # on initializing a RedisCluster, a COMMAND call may be emitted
        payloads = [item.payload for item in items]
        parent_span = payloads[-1]
        redis_spans = payloads[:-1]
        assert parent_span["name"] == "custom parent"
        assert len(redis_spans) in (1, 2)
        assert len(redis_spans) == 1 or redis_spans[0]["name"] == "COMMAND"

        span = redis_spans[-1]
        assert span["name"] == description
        attrs = span["attributes"]
        assert attrs["sentry.op"] == "db.redis"
        assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
        assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
        # ClusterNode converts localhost to 127.0.0.1
        assert attrs[SPANDATA.SERVER_ADDRESS] == "127.0.0.1"
        assert attrs[SPANDATA.SERVER_PORT] == 6379
        assert attrs[SPANDATA.DB_OPERATION_NAME] == "SET"
        assert attrs["db.redis.key"] == "bar"
    else:
        events = capture_events()
        with start_transaction():
            rc = redis.RedisCluster(host="localhost", port=6379)
            rc.set("bar", 1)

        (event,) = events
        spans = event["spans"]

        # on initializing a RedisCluster, a COMMAND call is made - this is not important for the test
        # but must be accounted for
        assert len(spans) in (1, 2)
        assert len(spans) == 1 or spans[0]["description"] == "COMMAND"

        span = spans[-1]
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
            "db.operation": "SET",
            "redis.command": "SET",
            "redis.is_cluster": True,
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
def test_rediscluster_pipeline(
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

    rc = redis.RedisCluster(host="localhost", port=6379)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            pipeline = rc.pipeline()
            pipeline.get("foo")
            pipeline.set("bar", 1)
            pipeline.set("baz", 2)
            pipeline.execute()
        sentry_sdk.flush()

        # on initializing a RedisCluster, a COMMAND call may be emitted
        payloads = [item.payload for item in items]
        parent_span = payloads[-1]
        redis_spans = payloads[:-1]
        assert parent_span["name"] == "custom parent"
        assert len(redis_spans) in (1, 2)
        assert len(redis_spans) == 1 or redis_spans[0]["name"] == "COMMAND"

        pipeline_span = redis_spans[-1]
        assert pipeline_span["name"] == "redis.pipeline.execute"
        attrs = pipeline_span["attributes"]
        assert attrs["sentry.op"] == "db.redis"
        assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
        assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
        # ClusterNode converts localhost to 127.0.0.1
        assert attrs[SPANDATA.SERVER_ADDRESS] == "127.0.0.1"
        assert attrs[SPANDATA.SERVER_PORT] == 6379
    else:
        events = capture_events()
        with start_transaction():
            pipeline = rc.pipeline()
            pipeline.get("foo")
            pipeline.set("bar", 1)
            pipeline.set("baz", 2)
            pipeline.execute()

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
            "redis.transaction": False,  # For Cluster, this is always False
            "redis.is_cluster": True,
        }


@pytest.mark.parametrize("span_streaming", [True, False])
def test_rediscluster_span_origin(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )

    rc = redis.RedisCluster(host="localhost", port=6379)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            # default case
            rc.set("somekey", "somevalue")

            # pipeline
            pipeline = rc.pipeline(transaction=False)
            pipeline.get("somekey")
            pipeline.set("anotherkey", 1)
            pipeline.execute()
        sentry_sdk.flush()

        payloads = [item.payload for item in items]
        parent_span = payloads[-1]
        redis_spans = payloads[:-1]

        assert parent_span["name"] == "custom parent"
        assert parent_span["attributes"]["sentry.origin"] == "manual"
        assert len(redis_spans) >= 2
        for span in redis_spans:
            assert span["attributes"]["sentry.origin"] == "auto.db.redis"
    else:
        events = capture_events()
        with start_transaction(name="custom_transaction"):
            # default case
            rc.set("somekey", "somevalue")

            # pipeline
            pipeline = rc.pipeline(transaction=False)
            pipeline.get("somekey")
            pipeline.set("anotherkey", 1)
            pipeline.execute()

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"

        for span in event["spans"]:
            assert span["origin"] == "auto.db.redis"
