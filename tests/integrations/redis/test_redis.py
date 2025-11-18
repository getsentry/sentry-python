from unittest import mock

import pytest
from fakeredis import FakeStrictRedis

from sentry_sdk import capture_message, start_transaction
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

    connection = FakeStrictRedis()

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
    sentry_init, capture_events, is_transaction, send_default_pii, expected_first_ten
):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        pipeline = connection.pipeline(transaction=is_transaction)
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]
    assert span["op"] == "db.redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"][SPANDATA.DB_SYSTEM] == "redis"
    assert span["data"]["redis.commands"] == {
        "count": 3,
        "first_ten": expected_first_ten,
    }
    assert span["tags"] == {
        "redis.transaction": is_transaction,
        "redis.is_cluster": False,
    }


def test_sensitive_data(sentry_init, capture_events):
    # fakeredis does not support the AUTH command, so we need to mock it
    with mock.patch(
        "sentry_sdk.integrations.redis.utils._COMMANDS_INCLUDING_SENSITIVE_DATA",
        ["get"],
    ):
        sentry_init(
            integrations=[RedisIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
        )
        events = capture_events()

        connection = FakeStrictRedis()
        with start_transaction():
            connection.get(
                "this is super secret"
            )  # because fakeredis does not support AUTH we use GET instead

        (event,) = events
        spans = event["spans"]
        assert spans[0]["op"] == "db.redis"
        assert spans[0]["description"] == "GET [Filtered]"


def test_pii_data_redacted(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        connection.set("somekey1", "my secret string1")
        connection.set("somekey2", "my secret string2")
        connection.get("somekey2")
        connection.delete("somekey1", "somekey2")

    (event,) = events
    spans = event["spans"]
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["description"] == "SET 'somekey1' [Filtered]"
    assert spans[1]["description"] == "SET 'somekey2' [Filtered]"
    assert spans[2]["description"] == "GET 'somekey2'"
    assert spans[3]["description"] == "DEL 'somekey1' [Filtered]"


def test_pii_data_sent(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        connection.set("somekey1", "my secret string1")
        connection.set("somekey2", "my secret string2")
        connection.get("somekey2")
        connection.delete("somekey1", "somekey2")

    (event,) = events
    spans = event["spans"]
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["description"] == "SET 'somekey1' 'my secret string1'"
    assert spans[1]["description"] == "SET 'somekey2' 'my secret string2'"
    assert spans[2]["description"] == "GET 'somekey2'"
    assert spans[3]["description"] == "DEL 'somekey1' 'somekey2'"


def test_no_data_truncation_by_default(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        long_string = "a" * 100000
        connection.set("somekey1", long_string)
        short_string = "b" * 10
        connection.set("somekey2", short_string)

    (event,) = events
    spans = event["spans"]
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["description"] == f"SET 'somekey1' '{long_string}'"
    assert spans[1]["description"] == f"SET 'somekey2' '{short_string}'"


def test_data_truncation_custom(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration(max_data_size=30)],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction():
        long_string = "a" * 100000
        connection.set("somekey1", long_string)
        short_string = "b" * 10
        connection.set("somekey2", short_string)

    (event,) = events
    spans = event["spans"]
    assert spans[0]["op"] == "db.redis"
    assert spans[0]["description"] == "SET 'somekey1' '%s..." % (
        long_string[: 30 - len("...") - len("SET 'somekey1' '")],
    )
    assert spans[1]["description"] == "SET 'somekey2' '%s'" % (short_string,)


def test_breadcrumbs(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration(max_data_size=30)],
        send_default_pii=True,
    )
    events = capture_events()

    connection = FakeStrictRedis()

    long_string = "a" * 100000
    connection.set("somekey1", long_string)
    short_string = "b" * 10
    connection.set("somekey2", short_string)

    capture_message("hi")

    (event,) = events
    crumbs = event["breadcrumbs"]["values"]

    assert crumbs[0] == {
        "message": "SET 'somekey1' 'aaaaaaaaaaa...",
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


def test_db_connection_attributes_client(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
    )
    events = capture_events()

    with start_transaction():
        connection = FakeStrictRedis(connection_pool=MOCK_CONNECTION_POOL)
        connection.get("foobar")

    (event,) = events
    (span,) = event["spans"]

    assert span["op"] == "db.redis"
    assert span["description"] == "GET 'foobar'"
    assert span["data"][SPANDATA.DB_SYSTEM] == "redis"
    assert span["data"][SPANDATA.DB_NAME] == "1"
    assert span["data"][SPANDATA.SERVER_ADDRESS] == "localhost"
    assert span["data"][SPANDATA.SERVER_PORT] == 63791


def test_db_connection_attributes_pipeline(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[RedisIntegration()],
    )
    events = capture_events()

    with start_transaction():
        connection = FakeStrictRedis(connection_pool=MOCK_CONNECTION_POOL)
        pipeline = connection.pipeline(transaction=False)
        pipeline.get("foo")
        pipeline.set("bar", 1)
        pipeline.set("baz", 2)
        pipeline.execute()

    (event,) = events
    (span,) = event["spans"]

    assert span["op"] == "db.redis"
    assert span["description"] == "redis.pipeline.execute"
    assert span["data"][SPANDATA.DB_SYSTEM] == "redis"
    assert span["data"][SPANDATA.DB_NAME] == "1"
    assert span["data"][SPANDATA.SERVER_ADDRESS] == "localhost"
    assert span["data"][SPANDATA.SERVER_PORT] == 63791


def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[RedisIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    connection = FakeStrictRedis()
    with start_transaction(name="custom_transaction"):
        # default case
        connection.set("somekey", "somevalue")

        # pipeline
        pipeline = connection.pipeline(transaction=False)
        pipeline.get("somekey")
        pipeline.set("anotherkey", 1)
        pipeline.execute()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"

    for span in event["spans"]:
        assert span["origin"] == "auto.db.redis"
