from unittest import mock

import pytest
from fakeredis import FakeRedis

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.redis import RedisIntegration

MOCK_CONNECTION_POOL = mock.MagicMock()
MOCK_CONNECTION_POOL.connection_kwargs = {
    "host": "localhost",
    "port": 63791,
    "db": 1,
}


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[RedisIntegration()])
    events = capture_events()

    connection = FakeRedis()

    connection.get("foobar")
    capture_message("hi")

    (event,) = events
    (crumb,) = event["breadcrumbs"]["values"]

    assert crumb == {
        "category": "redis",
        "message": "GET 'foobar'",
        "data": {
            "redis.key": "foobar",
            "redis.command": "GET",
            "redis.is_cluster": False,
            "db.operation": "GET",
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
def test_redis_pipeline(
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
        pipeline.execute()

    sentry_sdk.flush()

    assert len(items) == 2
    pipeline_span, parent_span = items[0].payload, items[1].payload

    assert parent_span["name"] == "custom parent"
    assert parent_span["is_segment"] is True

    assert pipeline_span["name"] == "redis.pipeline.execute"
    assert pipeline_span["attributes"]["sentry.op"] == "db.redis"
    assert pipeline_span["attributes"]["sentry.origin"] == "auto.db.redis"
    assert pipeline_span["attributes"][SPANDATA.DB_SYSTEM_NAME] == "redis"


@pytest.mark.parametrize(
    "data_collection, expected_first_ten",
    [
        (
            {"database_query_data": False},
            ["GET 'foo'", "SET 'bar'", "SET 'baz'"],
        ),
        (
            {"database_query_data": True},
            ["GET 'foo'", "SET 'bar' 1", "SET 'baz' 2"],
        ),
    ],
)
def test_redis_pipeline_data_collection(
    sentry_init,
    capture_events,
    capture_items,
    data_collection,
    expected_first_ten,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        _experiments={"data_collection": data_collection},
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        pipeline = connection.pipeline(transaction=False)
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    sentry_sdk.flush()

    assert len(items) == 2
    pipeline_span, parent_span = items[0].payload, items[1].payload

    assert parent_span["name"] == "custom parent"
    assert pipeline_span["name"] == "redis.pipeline.execute"
    assert pipeline_span["attributes"]["sentry.op"] == "db.redis"


def test_sensitive_data(
    sentry_init,
    capture_events,
    capture_items,
):
    # fakeredis does not support the AUTH command, so we need to mock it
    with mock.patch(
        "sentry_sdk.integrations.redis.utils._COMMANDS_INCLUDING_SENSITIVE_DATA",
        ["get"],
    ):
        sentry_init(
            integrations=[RedisIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
            trace_lifecycle="stream",
        )

        connection = FakeRedis()

        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="custom parent"):
            connection.get("this is super secret")
        sentry_sdk.flush()

        assert len(items) == 2
        redis_span, parent_span = items[0].payload, items[1].payload

        assert parent_span["name"] == "custom parent"
        assert redis_span["name"] == "GET [Filtered]"
        assert redis_span["attributes"][SPANDATA.DB_QUERY_TEXT] == "GET [Filtered]"
        assert redis_span["attributes"]["sentry.op"] == "db.redis"


def test_pii_data_redacted(
    sentry_init,
    capture_events,
    capture_items,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection.set("somekey1", "my secret string1")
        connection.set("somekey2", "my secret string2")
        connection.get("somekey2")
        connection.delete("somekey1", "somekey2")

    sentry_sdk.flush()

    assert len(items) == 5
    set1, set2, get, delete, parent = [item.payload for item in items]

    assert parent["name"] == "custom parent"
    assert set1["name"] == "SET 'somekey1' [Filtered]"
    assert set1["attributes"][SPANDATA.DB_QUERY_TEXT] == "SET 'somekey1' [Filtered]"
    assert set1["attributes"]["sentry.op"] == "db.redis"
    assert set2["name"] == "SET 'somekey2' [Filtered]"
    assert set2["attributes"][SPANDATA.DB_QUERY_TEXT] == "SET 'somekey2' [Filtered]"
    assert get["name"] == "GET 'somekey2'"
    assert delete["name"] == "DEL 'somekey1' [Filtered]"


@pytest.mark.parametrize(
    "data_collection, expected_description",
    [
        ({"database_query_data": False}, "SET 'somekey1'"),
        ({"database_query_data": True}, "SET 'somekey1' 'my secret string1'"),
        ({}, "SET 'somekey1' 'my secret string1'"),
    ],
    ids=[
        "database_query_data_disabled",
        "database_query_data_enabled",
        "database_query_data_not_provided_uses_defaults",
    ],
)
def test_data_collection_database_query_data(
    sentry_init,
    capture_events,
    capture_items,
    data_collection,
    expected_description,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
        _experiments={"data_collection": data_collection},
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection.set("somekey1", "my secret string1")

    sentry_sdk.flush()

    assert len(items) == 2
    set_span, parent = [item.payload for item in items]

    assert parent["name"] == "custom parent"
    assert set_span["name"] == expected_description
    assert set_span["attributes"][SPANDATA.DB_QUERY_TEXT] == expected_description
    assert set_span["attributes"]["sentry.op"] == "db.redis"


@pytest.mark.parametrize(
    "data_collection, send_default_pii, expected_description",
    [
        ({"database_query_data": False}, True, "SET 'somekey1'"),
        (
            {"database_query_data": True},
            False,
            "SET 'somekey1' 'my secret string1'",
        ),
    ],
)
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_database_query_data_takes_precedence_over_send_default_pii(
    sentry_init,
    capture_events,
    capture_items,
    data_collection,
    send_default_pii,
    expected_description,
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
        _experiments={"data_collection": data_collection},
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection.set("somekey1", "my secret string1")

    sentry_sdk.flush()

    assert len(items) == 2
    set_span, parent = [item.payload for item in items]

    assert parent["name"] == "custom parent"
    assert set_span["name"] == expected_description
    assert set_span["attributes"][SPANDATA.DB_QUERY_TEXT] == expected_description
    assert set_span["attributes"]["sentry.op"] == "db.redis"


def test_pii_data_sent(sentry_init, capture_items):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection.set("somekey1", "my secret string1")
        connection.set("somekey2", "my secret string2")
        connection.get("somekey2")
        connection.delete("somekey1", "somekey2")

    sentry_sdk.flush()

    assert len(items) == 5
    set1, set2, get, delete, parent = [item.payload for item in items]

    assert parent["name"] == "custom parent"
    assert set1["name"] == "SET 'somekey1' 'my secret string1'"
    assert (
        set1["attributes"][SPANDATA.DB_QUERY_TEXT]
        == "SET 'somekey1' 'my secret string1'"
    )
    assert set1["attributes"]["sentry.op"] == "db.redis"
    assert set2["name"] == "SET 'somekey2' 'my secret string2'"
    assert (
        set2["attributes"][SPANDATA.DB_QUERY_TEXT]
        == "SET 'somekey2' 'my secret string2'"
    )
    assert get["name"] == "GET 'somekey2'"
    assert delete["name"] == "DEL 'somekey1' 'somekey2'"


def test_no_data_truncation_by_default(sentry_init, capture_items):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()
    long_string = "a" * 100000
    short_string = "b" * 10

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection.set("somekey1", long_string)
        connection.set("somekey2", short_string)

    sentry_sdk.flush()

    assert len(items) == 3
    set1, set2, parent = [item.payload for item in items]

    assert parent["name"] == "custom parent"
    assert set1["name"] == f"SET 'somekey1' '{long_string}'"
    assert (
        set1["attributes"][SPANDATA.DB_QUERY_TEXT] == f"SET 'somekey1' '{long_string}'"
    )
    assert set1["attributes"]["sentry.op"] == "db.redis"
    assert set2["name"] == f"SET 'somekey2' '{short_string}'"
    assert (
        set2["attributes"][SPANDATA.DB_QUERY_TEXT] == f"SET 'somekey2' '{short_string}'"
    )


def test_breadcrumbs(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        send_default_pii=True,
    )
    events = capture_events()

    connection = FakeRedis()

    long_string = "a" * 30
    connection.set("somekey1", long_string)
    short_string = "b" * 10
    connection.set("somekey2", short_string)

    capture_message("hi")

    (event,) = events
    crumbs = event["breadcrumbs"]["values"]

    assert crumbs[0] == {
        "message": "SET 'somekey1' '" + 30 * "a" + "'",
        "type": "redis",
        "category": "redis",
        "data": {
            "db.operation": "SET",
            "redis.is_cluster": False,
            "redis.command": "SET",
            "redis.key": "somekey1",
        },
        "timestamp": crumbs[0]["timestamp"],
    }
    assert crumbs[1] == {
        "message": "SET 'somekey2' 'bbbbbbbbbb'",
        "type": "redis",
        "category": "redis",
        "data": {
            "db.operation": "SET",
            "redis.is_cluster": False,
            "redis.command": "SET",
            "redis.key": "somekey2",
        },
        "timestamp": crumbs[1]["timestamp"],
    }


def test_db_connection_attributes_client(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection = FakeRedis(connection_pool=MOCK_CONNECTION_POOL)
        connection.get("foobar")

    sentry_sdk.flush()

    assert len(items) == 2
    redis_span, parent_span = items[0].payload, items[1].payload

    assert parent_span["name"] == "custom parent"
    assert redis_span["name"] == "GET 'foobar'"
    attrs = redis_span["attributes"]
    assert attrs["sentry.op"] == "db.redis"
    assert attrs[SPANDATA.DB_QUERY_TEXT] == "GET 'foobar'"
    assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
    assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
    assert attrs[SPANDATA.DB_NAMESPACE] == "1"
    assert attrs[SPANDATA.SERVER_ADDRESS] == "localhost"
    assert attrs[SPANDATA.SERVER_PORT] == 63791


def test_db_connection_attributes_pipeline(sentry_init, capture_items):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        connection = FakeRedis(connection_pool=MOCK_CONNECTION_POOL)
        pipeline = connection.pipeline(transaction=False)
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    sentry_sdk.flush()

    assert len(items) == 2
    pipeline_span, parent_span = items[0].payload, items[1].payload

    assert parent_span["name"] == "custom parent"
    assert pipeline_span["name"] == "redis.pipeline.execute"
    attrs = pipeline_span["attributes"]
    assert attrs["sentry.op"] == "db.redis"
    assert attrs[SPANDATA.DB_SYSTEM_NAME] == "redis"
    assert attrs[SPANDATA.DB_DRIVER_NAME] == "redis-py"
    assert attrs[SPANDATA.DB_NAMESPACE] == "1"
    assert attrs[SPANDATA.SERVER_ADDRESS] == "localhost"
    assert attrs[SPANDATA.SERVER_PORT] == 63791


def test_span_origin(sentry_init, capture_items):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    connection = FakeRedis()

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        # default case
        connection.set("somekey", "somevalue")

        # pipeline
        pipeline = connection.pipeline(transaction=False)
        pipeline.get("somekey")
        pipeline.set("anotherkey", 1)
        pipeline.execute()

    sentry_sdk.flush()

    assert len(items) == 3
    set_span, pipeline_span, parent_span = [item.payload for item in items]

    assert parent_span["name"] == "custom parent"
    assert parent_span["attributes"]["sentry.origin"] == "manual"
    assert set_span["attributes"]["sentry.origin"] == "auto.db.redis"
    assert set_span["attributes"][SPANDATA.DB_QUERY_TEXT] == "SET 'somekey' [Filtered]"
    assert pipeline_span["attributes"]["sentry.origin"] == "auto.db.redis"
