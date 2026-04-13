"""
Tests need pytest-asyncio installed.

Tests need a local MySQL instance running. This can be done using:
```sh
docker run --rm --name some-mysql -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=test -d -p 3306:3306 mysql:8.0
```

The tests use the following credentials to establish a database connection.
"""

import os
import datetime
from contextlib import contextmanager
from unittest import mock

import aiomysql
import pytest
import pytest_asyncio

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.aiomysql import AioMySQLIntegration
from sentry_sdk.consts import SPANDATA
from sentry_sdk.tracing_utils import record_sql_queries
from tests.conftest import ApproxDict

MYSQL_HOST = os.getenv("SENTRY_PYTHON_TEST_MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("SENTRY_PYTHON_TEST_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("SENTRY_PYTHON_TEST_MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("SENTRY_PYTHON_TEST_MYSQL_PASSWORD", "root")
MYSQL_DB_BASE = os.getenv("SENTRY_PYTHON_TEST_MYSQL_DB", "test")


def _get_db_name():
    pid = os.getpid()
    return f"{MYSQL_DB_BASE}_{pid}"


MYSQL_DB = _get_db_name()

CRUMBS_CONNECT = {
    "category": "query",
    "data": ApproxDict(
        {
            "db.name": MYSQL_DB,
            "db.system": "mysql",
            "db.user": MYSQL_USER,
            "server.address": MYSQL_HOST,
            "server.port": MYSQL_PORT,
        }
    ),
    "message": "connect",
    "type": "default",
}


@pytest_asyncio.fixture(autouse=True)
async def _clean_mysql():
    conn = await aiomysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}`")
            await cur.execute(f"USE `{MYSQL_DB}`")
            await cur.execute("DROP TABLE IF EXISTS users")
            await cur.execute(
                """
                CREATE TABLE users(
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255),
                    password VARCHAR(255),
                    dob DATE
                )
                """
            )
    finally:
        conn.close()


def _connect_args():
    return {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "db": MYSQL_DB,
        "autocommit": True,
    }


@pytest.mark.asyncio
async def test_connect(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())
    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [CRUMBS_CONNECT]


@pytest.mark.asyncio
async def test_execute(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES ('Alice', 'pw', '1990-12-25')",
        )
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
        )
        await cur.execute("SELECT * FROM users WHERE name = %s", ("Bob",))
        row = await cur.fetchone()
        assert row[1] == "Bob"

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {},
            "message": "INSERT INTO users(name, password, dob) VALUES ('Alice', 'pw', '1990-12-25')",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT * FROM users WHERE name = %s",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_execute_many(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            [
                ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
                ("Alice", "pw", datetime.date(1990, 12, 25)),
            ],
        )

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_execute_many_non_insert(sentry_init, capture_events) -> None:
    """Test executemany with non-INSERT queries (falls back to row-by-row)."""
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        # Pre-populate users table
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            ("Alice", "pw1", datetime.date(1990, 1, 1)),
        )
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            ("Bob", "pw2", datetime.date(1991, 2, 2)),
        )
        # Non-INSERT executemany — uses row-by-row fallback internally
        await cur.executemany(
            "UPDATE users SET password = %s WHERE name = %s",
            [
                ("new_pw_1", "Alice"),
                ("new_pw_2", "Bob"),
            ],
        )

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    # Should have: connect + INSERT*2 + single executemany span (no double-recording)
    crumbs = event["breadcrumbs"]["values"]
    query_crumbs = [c for c in crumbs if c["category"] == "query"]
    executemany_crumbs = [c for c in query_crumbs if c.get("data", {}).get("db.executemany")]
    # Only ONE executemany breadcrumb — no duplicates from internal execute calls
    assert len(executemany_crumbs) == 1
    assert "UPDATE users SET password = %s" in executemany_crumbs[0]["message"]


@pytest.mark.asyncio
async def test_record_params(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration(record_params=True)],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
        )

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {
                "db.params": ["Bob", "secret_pw", "datetime.date(1984, 3, 1)"],
                "db.paramstyle": "format",
            },
            "message": "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_cursor_context_manager(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            [
                ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
                ("Alice", "pw", datetime.date(1990, 12, 25)),
            ],
        )
        await cur.execute(
            "SELECT * FROM users WHERE dob > %s",
            (datetime.date(1970, 1, 1),),
        )
        rows = await cur.fetchall()
        assert len(rows) == 2

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    crumbs = event["breadcrumbs"]["values"]
    assert crumbs[0] == CRUMBS_CONNECT
    assert crumbs[1]["category"] == "query"
    assert "INSERT" in crumbs[1]["message"]
    assert crumbs[2]["category"] == "query"
    assert "SELECT" in crumbs[2]["message"]


@pytest.mark.asyncio
async def test_cursor_async_iteration(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn = await aiomysql.connect(**_connect_args())

    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
            ("Charlie", "pw3", datetime.date(1995, 5, 15)),
        )
        await cur.execute("SELECT * FROM users WHERE name = %s", ("Charlie",))
        async for row in cur:
            assert row[1] == "Charlie"

    conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    crumbs = event["breadcrumbs"]["values"]
    assert crumbs[0] == CRUMBS_CONNECT
    assert len([c for c in crumbs if c["category"] == "query"]) >= 2


@pytest.mark.asyncio
async def test_connection_pool(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AioMySQLIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    pool_size = 2

    pool = await aiomysql.create_pool(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        db=MYSQL_DB,
        autocommit=True,
        minsize=pool_size,
        maxsize=pool_size,
    )

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users(name, password, dob) VALUES (%s, %s, %s)",
                ("Dave", "pw4", datetime.date(1988, 7, 20)),
            )
            await cur.execute("SELECT * FROM users WHERE name = %s", ("Dave",))
            row = await cur.fetchone()
            assert row is not None
            assert row[1] == "Dave"

    pool.close()
    await pool.wait_closed()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    crumbs = event["breadcrumbs"]["values"]
    # Verify queries were captured
    query_crumbs = [c for c in crumbs if c["category"] == "query"]
    assert len(query_crumbs) >= 2  # INSERT + SELECT


@pytest.mark.asyncio
async def test_query_source_disabled(sentry_init, capture_events):
    sentry_options = {
        "integrations": [AioMySQLIntegration()],
        "enable_tracing": True,
        "enable_db_query_source": False,
        "db_query_source_threshold_ms": 0,
    }

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

        conn.close()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("INSERT INTO")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_db_query_source", [None, True])
async def test_query_source_enabled(
    sentry_init, capture_events, enable_db_query_source
):
    sentry_options = {
        "integrations": [AioMySQLIntegration()],
        "enable_tracing": True,
        "db_query_source_threshold_ms": 0,
    }
    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source

    sentry_init(**sentry_options)

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

        conn.close()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("INSERT INTO")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.asyncio
async def test_query_source(sentry_init, capture_events):
    sentry_init(
        integrations=[AioMySQLIntegration()],
        enable_tracing=True,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

        conn.close()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("INSERT INTO")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE)
        == "tests.integrations.aiomysql.test_aiomysql"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/aiomysql/test_aiomysql.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_query_source"


@pytest.mark.asyncio
async def test_no_query_source_if_duration_too_short(sentry_init, capture_events):
    sentry_init(
        integrations=[AioMySQLIntegration()],
        enable_tracing=True,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = await aiomysql.connect(**_connect_args())

        @contextmanager
        def fake_record_sql_queries(*args, **kwargs):
            with record_sql_queries(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            yield span

        async with conn.cursor() as cur:
            with mock.patch(
                "sentry_sdk.integrations.aiomysql.record_sql_queries",
                fake_record_sql_queries,
            ):
                await cur.execute(
                    "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                )

        conn.close()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("INSERT INTO")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO not in data
    assert SPANDATA.CODE_NAMESPACE not in data
    assert SPANDATA.CODE_FILEPATH not in data
    assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.asyncio
async def test_query_source_if_duration_over_threshold(sentry_init, capture_events):
    sentry_init(
        integrations=[AioMySQLIntegration()],
        enable_tracing=True,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn = await aiomysql.connect(**_connect_args())

        @contextmanager
        def fake_record_sql_queries(*args, **kwargs):
            with record_sql_queries(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            yield span

        async with conn.cursor() as cur:
            with mock.patch(
                "sentry_sdk.integrations.aiomysql.record_sql_queries",
                fake_record_sql_queries,
            ):
                await cur.execute(
                    "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                )

        conn.close()

    (event,) = events

    span = event["spans"][-1]
    assert span["description"].startswith("INSERT INTO")

    data = span.get("data", {})

    assert SPANDATA.CODE_LINENO in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FILEPATH in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(SPANDATA.CODE_LINENO)) == int
    assert data.get(SPANDATA.CODE_LINENO) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE)
        == "tests.integrations.aiomysql.test_aiomysql"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/aiomysql/test_aiomysql.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert (
        data.get(SPANDATA.CODE_FUNCTION)
        == "test_query_source_if_duration_over_threshold"
    )


@pytest.mark.asyncio
async def test_span_origin(sentry_init, capture_events):
    sentry_init(
        integrations=[AioMySQLIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.execute("SELECT 2")

        conn.close()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"

    for span in event["spans"]:
        assert span["origin"] == "auto.db.aiomysql"


@pytest.mark.asyncio
async def test_multiline_query_description_normalized(sentry_init, capture_events):
    sentry_init(
        integrations=[AioMySQLIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    id,
                    name
                FROM
                    users
                WHERE
                    name = 'Alice'
                """
            )

        conn.close()

    (event,) = events

    spans = [
        s
        for s in event["spans"]
        if s["op"] == "db" and "SELECT" in s.get("description", "")
    ]
    assert len(spans) == 1
    assert spans[0]["description"] == "SELECT id, name FROM users WHERE name = 'Alice'"


@pytest.mark.asyncio
async def test_before_send_transaction_sees_normalized_description(
    sentry_init, capture_events
):
    def before_send_transaction(event, hint):
        for span in event.get("spans", []):
            desc = span.get("description", "")
            if "SELECT id, name FROM users" in desc:
                span["description"] = "filtered"
        return event

    sentry_init(
        integrations=[AioMySQLIntegration()],
        traces_sample_rate=1.0,
        before_send_transaction=before_send_transaction,
    )
    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    id,
                    name
                FROM
                    users
                """
            )

        conn.close()

    (event,) = events
    spans = [
        s
        for s in event["spans"]
        if s["op"] == "db" and "filtered" in s.get("description", "")
    ]

    assert len(spans) == 1
    assert spans[0]["description"] == "filtered"


@pytest.mark.asyncio
async def test_db_data_on_spans(sentry_init, capture_events):
    """Test that database connection data is properly set on spans."""
    sentry_init(
        integrations=[AioMySQLIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn = await aiomysql.connect(**_connect_args())

        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")

        conn.close()

    (event,) = events

    db_spans = [s for s in event["spans"] if s["op"] == "db"]
    assert len(db_spans) > 0

    query_span = [s for s in db_spans if "SELECT" in s.get("description", "")][0]
    assert query_span["data"].get(SPANDATA.DB_SYSTEM) == "mysql"
    assert query_span["data"].get(SPANDATA.SERVER_ADDRESS) == MYSQL_HOST
    assert query_span["data"].get(SPANDATA.SERVER_PORT) == MYSQL_PORT
    assert query_span["data"].get(SPANDATA.DB_NAME) == MYSQL_DB
    assert query_span["data"].get(SPANDATA.DB_USER) == MYSQL_USER
