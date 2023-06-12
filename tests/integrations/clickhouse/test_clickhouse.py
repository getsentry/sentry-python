"""
Tests need a local clickhouse instance running, this can best be done using
```sh
docker run -d -p 18123:8123 -p9000:9000 --name clickhouse-test --ulimit nofile=262144:262144 --rm clickhouse/clickhouse-server
```
"""
import clickhouse_driver
from clickhouse_driver import Client, connect

from sentry_sdk import capture_message
from sentry_sdk.integrations.clickhouse_driver import ClickhouseDriverIntegration

EXPECT_PARAMS_IN_SELECT = True
if clickhouse_driver.VERSION < (0, 2, 6):
    EXPECT_PARAMS_IN_SELECT = False


def test_clickhouse_client(sentry_init, capture_events) -> None:
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

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        {
            "category": "query",
            "data": {"db.result": []},
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.result": []},
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [{"x": 100}]},
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [[170], [200]]},
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": {"minv": 150}, "db.result": [[370]]}
            if EXPECT_PARAMS_IN_SELECT
            else {"db.result": [[370]]},
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]


def test_clickhouse_dbapi(sentry_init, capture_events) -> None:
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

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        {
            "category": "query",
            "data": {"db.result": [[], []]},
            "message": "DROP TABLE IF EXISTS test",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.result": [[], []]},
            "message": "CREATE TABLE test (x Int32) ENGINE = Memory",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [{"x": 100}]},
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.params": [[170], [200]]},
            "message": "INSERT INTO test (x) VALUES",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.params": {"minv": 150},
                "db.result": [[["370"]], [["'sum(x)'", "'Int64'"]]],
            }
            if EXPECT_PARAMS_IN_SELECT
            else {"db.result": [[["370"]], [["'sum(x)'", "'Int64'"]]]},
            "message": "SELECT sum(x) FROM test WHERE x > 150",
            "type": "default",
        },
    ]
