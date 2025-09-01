"""
Tests need a local clickhouse instance running, this can best be done using
```sh
docker run -d -e CLICKHOUSE_SKIP_USER_SETUP=1 -p 8123:8123 -p 9000:9000 --name clickhouse-test --ulimit nofile=262144:262144 --rm clickhouse
```
"""

import clickhouse_driver
from clickhouse_driver import Client, connect

from sentry_sdk import start_span, capture_message
from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration
from tests.conftest import ApproxDict

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

    for crumb in expected_breadcrumbs:
        crumb["data"] = ApproxDict(crumb["data"])

    for crumb in event["breadcrumbs"]["values"]:
        crumb.pop("timestamp", None)

    actual_query_breadcrumbs = [
        breadcrumb
        for breadcrumb in event["breadcrumbs"]["values"]
        if breadcrumb["category"] == "query"
    ]

    assert actual_query_breadcrumbs == expected_breadcrumbs


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

    for crumb in expected_breadcrumbs:
        crumb["data"] = ApproxDict(crumb["data"])

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

    with start_span(name="test_clickhouse_transaction") as transaction:
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
            "origin": "auto.db.clickhouse_driver",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "sentry.name": "DROP TABLE IF EXISTS test",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "sentry.name": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "sentry.name": "SELECT sum(x) FROM test WHERE x > 150",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in expected_spans:
        span["data"] = ApproxDict(span["data"])

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)
        span.pop("same_process_as_parent", None)
        span.pop("status", None)

    assert event["spans"] == expected_spans


def test_clickhouse_spans_with_generator(sentry_init, capture_events):
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )
    events = capture_events()

    # Use a generator to test that the integration obtains values from the generator,
    # without consuming the generator.
    values = ({"x": i} for i in range(3))

    with start_span(name="test_clickhouse_transaction"):
        client = Client("localhost")
        client.execute("DROP TABLE IF EXISTS test")
        client.execute("CREATE TABLE test (x Int32) ENGINE = Memory")
        client.execute("INSERT INTO test (x) VALUES", values)
        res = client.execute("SELECT x FROM test")

    # Verify that the integration did not consume the generator
    assert res == [(0,), (1,), (2,)]

    (event,) = events
    spans = event["spans"]

    [span] = [
        span for span in spans if span["description"] == "INSERT INTO test (x) VALUES"
    ]

    assert span["data"]["db.params"] == '[{"x": 0}, {"x": 1}, {"x": 2}]'


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

    with start_span(name="test_clickhouse_transaction") as transaction:
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
            "origin": "auto.db.clickhouse_driver",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "sentry.name": "DROP TABLE IF EXISTS test",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
                "db.query.text": "DROP TABLE IF EXISTS test",
                "db.result": [],
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "sentry.name": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "db.result": [],
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "INSERT INTO test (x) VALUES",
                "db.params": '[{"x": 100}]',
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "INSERT INTO test (x) VALUES",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "sentry.name": "SELECT sum(x) FROM test WHERE x > 150",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.params": '{"minv": 150}',
                "db.query.text": "SELECT sum(x) FROM test WHERE x > 150",
                "db.result": "[[370]]",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in expected_spans:
        span["data"] = ApproxDict(span["data"])

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)
        span.pop("same_process_as_parent", None)
        span.pop("status", None)

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

    for crumb in expected_breadcrumbs:
        crumb["data"] = ApproxDict(crumb["data"])

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

    for crumb in expected_breadcrumbs:
        crumb["data"] = ApproxDict(crumb["data"])

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

    with start_span(name="test_clickhouse_transaction") as transaction:
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
            "origin": "auto.db.clickhouse_driver",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "sentry.name": "DROP TABLE IF EXISTS test",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "sentry.name": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "sentry.name": "SELECT sum(x) FROM test WHERE x > 150",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in expected_spans:
        span["data"] = ApproxDict(span["data"])

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)
        span.pop("status", None)

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

    with start_span(name="test_clickhouse_transaction") as transaction:
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
            "origin": "auto.db.clickhouse_driver",
            "description": "DROP TABLE IF EXISTS test",
            "data": {
                "sentry.name": "DROP TABLE IF EXISTS test",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "DROP TABLE IF EXISTS test",
                "db.result": "[[], []]",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "data": {
                "sentry.name": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "CREATE TABLE test (x Int32) ENGINE = Memory",
                "db.result": "[[], []]",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "INSERT INTO test (x) VALUES",
                "db.params": '[{"x": 100}]',
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "INSERT INTO test (x) VALUES",
            "data": {
                "sentry.name": "INSERT INTO test (x) VALUES",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "INSERT INTO test (x) VALUES",
                "db.params": "[[170], [200]]",
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
        {
            "op": "db",
            "origin": "auto.db.clickhouse_driver",
            "description": "SELECT sum(x) FROM test WHERE x > 150",
            "data": {
                "sentry.name": "SELECT sum(x) FROM test WHERE x > 150",
                "sentry.origin": "auto.db.clickhouse_driver",
                "sentry.op": "db",
                "db.system": "clickhouse",
                "db.name": "",
                "db.user": "default",
                "db.query.text": "SELECT sum(x) FROM test WHERE x > 150",
                "db.params": '{"minv": 150}',
                "db.result": '[[[370]], [["sum(x)", "Int64"]]]',
                "server.address": "localhost",
                "server.port": 9000,
            },
            "trace_id": transaction_trace_id,
            "parent_span_id": transaction_span_id,
        },
    ]

    if not EXPECT_PARAMS_IN_SELECT:
        expected_spans[-1]["data"].pop("db.params", None)

    for span in expected_spans:
        span["data"] = ApproxDict(span["data"])

    for span in event["spans"]:
        span.pop("span_id", None)
        span.pop("start_timestamp", None)
        span.pop("timestamp", None)
        span.pop("same_process_as_parent", None)
        span.pop("status", None)

    assert event["spans"] == expected_spans


def test_span_origin(sentry_init, capture_events, capture_envelopes) -> None:
    sentry_init(
        integrations=[ClickhouseDriverIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_span(name="test_clickhouse_transaction"):
        conn = connect("clickhouse://localhost")
        cursor = conn.cursor()
        cursor.execute("SELECT 1")

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.clickhouse_driver"
