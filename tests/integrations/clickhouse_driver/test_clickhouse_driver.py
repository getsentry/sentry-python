"""
Tests need a local clickhouse instance running, this can best be done using
```sh
docker run -d -p 18123:8123 -p9000:9000 --name clickhouse-test --ulimit nofile=262144:262144 --rm clickhouse/clickhouse-server
```
"""
import clickhouse_driver
from clickhouse_driver import Client, connect

from sentry_sdk import start_transaction, capture_message
from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration

EXPECT_PARAMS_IN_SELECT = True
if clickhouse_driver.VERSION < (0, 2, 6):
    EXPECT_PARAMS_IN_SELECT = False


def test_clickhouse_client_breadcrumbs(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    client = Client("localhost")
    client.execute("DROP TABLE IF EXISTS test")
    client.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
    client.execute("INSERT INTO test (x) VALUES", [{"x": 100}])
    client.execute("INSERT INTO test (x) VALUES", [[170], [200]])

    res = client.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
    assert res[0][0] == 370

    capture_message("hi")

    (event,) = events

    expected_breadcrumbs = [
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_breadcrumbs[-1]["data"].pop("db.params", None)

    for crumb in event["breadcrumbs"]["values"]:
        crumb.pop("timestamp", None)

    assert event["breadcrumbs"]["values"] == expected_breadcrumbs


def test_clickhouse_client_breadcrumbs_with_pii(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        send_default_pii=True,
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    client = Client("localhost")
    client.execute("DROP TABLE IF EXISTS test")
    client.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
    client.execute("INSERT INTO test (x) VALUES", [{"x": 100}])
    client.execute("INSERT INTO test (x) VALUES", [[170], [200]])

    res = client.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
    assert res[0][0] == 370

    capture_message("hi")

    (event,) = events

    expected_breadcrumbs = [
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [],
            },
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [],
            },
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [{"x": 100}],
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [[170], [200]],
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [[370]],
                "db.params": {"minv": 150},
            },
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_breadcrumbs[-1]["data"].pop("db.params", None)

    for crumb in event["breadcrumbs"]["values"]:
        crumb.pop("timestamp", None)

    assert event["breadcrumbs"]["values"] == expected_breadcrumbs


def test_clickhouse_client_spans(
    sentry_init, capture_events, capture_envelopes
) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        _experiments={"record_sql_params": True},
        traces_sample_rate=1.0,
    )
    events = capture_events()

    transaction_trace_id = None
    transaction_span_id = None

    with start_transaction(name="test_clickhouse_transaction") as transaction:
        transaction_trace_id = transaction.trace_id
        transaction_span_id = transaction.span_id

        client = Client("localhost")
        client.execute("DROP TABLE IF EXISTS test")
        client.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
        client.execute("INSERT INTO test (x) VALUES", [{"x": 100}])
        client.execute("INSERT INTO test (x) VALUES", [[170], [200]])

        res = client.execute(
            "SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150}
        )
        assert res[0][0] == 370

    (event,) = events

    expected_spans = [
        {
            "op": "db",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)

    assert event["spans"] == expected_spans


def test_clickhouse_client_spans_with_pii(
    sentry_init, capture_events, capture_envelopes
) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        _experiments={"record_sql_params": True},
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    transaction_trace_id = None
    transaction_span_id = None

    with start_transaction(name="test_clickhouse_transaction") as transaction:
        transaction_trace_id = transaction.trace_id
        transaction_span_id = transaction.span_id

        client = Client("localhost")
        client.execute("DROP TABLE IF EXISTS test")
        client.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
        client.execute("INSERT INTO test (x) VALUES", [{"x": 100}])
        client.execute("INSERT INTO test (x) VALUES", [[170], [200]])

        res = client.execute(
            "SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150}
        )
        assert res[0][0] == 370

    (event,) = events

    expected_spans = [
        {
            "op": "db",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [{"x": 100}],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [[170], [200]],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": {"minv": 150},
                "db.result": [[370]],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)

    assert event["spans"] == expected_spans


def test_clickhouse_dbapi_breadcrumbs(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
    )
    events = capture_events()

    conn = connect("clickhouse://localhost")
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS test")
    cursor.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
    cursor.executemany("INSERT INTO test (x) VALUES", [{"x": 100}])
    cursor.executemany("INSERT INTO test (x) VALUES", [[170], [200]])
    cursor.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
    res = cursor.fetchall()

    assert res[0][0] == 370

    capture_message("hi")

    (event,) = events

    expected_breadcrumbs = [
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_breadcrumbs[-1]["data"].pop("db.params", None)

    for crumb in event["breadcrumbs"]["values"]:
        crumb.pop("timestamp", None)

    assert event["breadcrumbs"]["values"] == expected_breadcrumbs


def test_clickhouse_dbapi_breadcrumbs_with_pii(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        send_default_pii=True,
    )
    events = capture_events()

    conn = connect("clickhouse://localhost")
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS test")
    cursor.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
    cursor.executemany("INSERT INTO test (x) VALUES", [{"x": 100}])
    cursor.executemany("INSERT INTO test (x) VALUES", [[170], [200]])
    cursor.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
    res = cursor.fetchall()

    assert res[0][0] == 370

    capture_message("hi")

    (event,) = events

    expected_breadcrumbs = [
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [[], []],
            },
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [[], []],
            },
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [{"x": 100}],
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [[170], [200]],
            },
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": {"minv": 150},
                "db.result": [[["370"]], [["'sum(x)'", "'Int64'"]]],
            },
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_breadcrumbs[-1]["data"].pop("db.params", None)

    for crumb in event["breadcrumbs"]["values"]:
        crumb.pop("timestamp", None)

    assert event["breadcrumbs"]["values"] == expected_breadcrumbs


def test_clickhouse_dbapi_spans(sentry_init, capture_events, capture_envelopes) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        _experiments={"record_sql_params": True},
        traces_sample_rate=1.0,
    )
    events = capture_events()

    transaction_trace_id = None
    transaction_span_id = None

    with start_transaction(name="test_clickhouse_transaction") as transaction:
        transaction_trace_id = transaction.trace_id
        transaction_span_id = transaction.span_id

        conn = connect("clickhouse://localhost")
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS test")
        cursor.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
        cursor.executemany("INSERT INTO test (x) VALUES", [{"x": 100}])
        cursor.executemany("INSERT INTO test (x) VALUES", [[170], [200]])
        cursor.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
        res = cursor.fetchall()

        assert res[0][0] == 370

    (event,) = events

    expected_spans = [
        {
            "op": "db",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)

    assert event["spans"] == expected_spans


def test_clickhouse_dbapi_spans_with_pii(
    sentry_init, capture_events, capture_envelopes
) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        _experiments={"record_sql_params": True},
        traces_sample_rate=1.0,
        send_default_pii=True,
    )
    events = capture_events()

    transaction_trace_id = None
    transaction_span_id = None

    with start_transaction(name="test_clickhouse_transaction") as transaction:
        transaction_trace_id = transaction.trace_id
        transaction_span_id = transaction.span_id

        conn = connect("clickhouse://localhost")
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS test")
        cursor.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
        cursor.executemany("INSERT INTO test (x) VALUES", [{"x": 100}])
        cursor.executemany("INSERT INTO test (x) VALUES", [[170], [200]])
        cursor.execute("SELECT sum(x) FROM test WHERE x > %(minv)i", {"minv": 150})
        res = cursor.fetchall()

        assert res[0][0] == 370

    (event,) = events

    expected_spans = [
        {
            "op": "db",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [[], []],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.result": [[], []],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [{"x": 100}],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": [[170], [200]],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.params": {"minv": 150},
                "db.result": [[[370]], [["sum(x)", "Int64"]]],
            },
            "same_process_as_parent": True,
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)

    assert event["spans"] == expected_spans
