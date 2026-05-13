"""
Tests need pytest-asyncio installed.

Tests need a local postgresql instance running, this can best be done using
```sh
docker run --rm --name some-postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=sentry -d -p 5432:5432 postgres
```

The tests use the following credentials to establish a database connection.
"""

import datetime
import os
from contextlib import contextmanager
from unittest import mock

import asyncpg
import pytest
import pytest_asyncio
from asyncpg import Connection, connect

import sentry_sdk
from sentry_sdk import capture_message, start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
from sentry_sdk.tracing_utils import record_sql_queries_supporting_streaming
from tests.conftest import ApproxDict

PG_HOST = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"))
PG_USER = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_USER", "postgres")
PG_PASSWORD = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_PASSWORD", "sentry")
PG_NAME_BASE = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_NAME", "postgres")


def _get_db_name():
    pid = os.getpid()
    return f"{PG_NAME_BASE}_{pid}"


PG_NAME = _get_db_name()

PG_CONNECTION_URI = "postgresql://{}:{}@{}/{}".format(
    PG_USER, PG_PASSWORD, PG_HOST, PG_NAME
)
CRUMBS_CONNECT = {
    "category": "query",
    "data": ApproxDict(
        {
            "db.name": PG_NAME,
            "db.system": "postgresql",
            "db.user": PG_USER,
            "db.driver.name": "asyncpg",
            "server.address": PG_HOST,
            "server.port": PG_PORT,
        }
    ),
    "message": "connect",
    "type": "default",
}


@pytest_asyncio.fixture(autouse=True)
async def _clean_pg():
    # Create the test database if it doesn't exist
    default_conn = await connect(
        "postgresql://{}:{}@{}".format(PG_USER, PG_PASSWORD, PG_HOST)
    )
    try:
        # Check if database exists, create if not
        result = await default_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", PG_NAME
        )
        if not result:
            await default_conn.execute(f'CREATE DATABASE "{PG_NAME}"')
    finally:
        await default_conn.close()

    # Now connect to our test database and set up the table
    conn = await connect(PG_CONNECTION_URI)
    await conn.execute("DROP TABLE IF EXISTS users")
    await conn.execute(
        """
            CREATE TABLE users(
                id serial PRIMARY KEY,
                name text,
                password text,
                dob date
            )
        """
    )
    await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_connect(
    sentry_init, capture_events, capture_items, span_streaming
) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("event")
    else:
        events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.close()

    capture_message("hi")

    if span_streaming:
        event = items[0].payload
    else:
        (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [CRUMBS_CONNECT]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_execute(
    sentry_init, capture_events, capture_items, span_streaming
) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("event")
    else:
        events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.execute(
        "INSERT INTO users(name, password, dob) VALUES ('Alice', 'pw', '1990-12-25')",
    )

    await conn.execute(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        "Bob",
        "secret_pw",
        datetime.date(1984, 3, 1),
    )

    row = await conn.fetchrow("SELECT * FROM users WHERE name = $1", "Bob")
    assert row == (2, "Bob", "secret_pw", datetime.date(1984, 3, 1))

    row = await conn.fetchrow("SELECT * FROM users WHERE name = 'Bob'")
    assert row == (2, "Bob", "secret_pw", datetime.date(1984, 3, 1))

    await conn.close()

    capture_message("hi")

    if span_streaming:
        event = items[0].payload
    else:
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
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT * FROM users WHERE name = $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT * FROM users WHERE name = 'Bob'",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_execute_many(
    sentry_init, capture_events, capture_items, span_streaming
) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={
            "record_sql_params": True,
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("event")
    else:
        events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.executemany(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        [
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
            ("Alice", "pw", datetime.date(1990, 12, 25)),
        ],
    )

    await conn.close()

    capture_message("hi")

    if span_streaming:
        event = items[0].payload
    else:
        (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_record_params(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration(record_params=True)],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.execute(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        "Bob",
        "secret_pw",
        datetime.date(1984, 3, 1),
    )

    await conn.close()

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
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_cursor(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.executemany(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        [
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
            ("Alice", "pw", datetime.date(1990, 12, 25)),
        ],
    )

    async with conn.transaction():
        # Postgres requires non-scrollable cursors to be created
        # and used in a transaction.
        async for record in conn.cursor(
            "SELECT * FROM users WHERE dob > $1", datetime.date(1970, 1, 1)
        ):
            print(record)

    await conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "BEGIN;", "type": "default"},
        {
            "category": "query",
            "data": {"db.cursor": mock.ANY},
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "COMMIT;", "type": "default"},
    ]


@pytest.mark.asyncio
async def test_cursor_manual(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.executemany(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        [
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
            ("Alice", "pw", datetime.date(1990, 12, 25)),
        ],
    )
    #
    async with conn.transaction():
        # Postgres requires non-scrollable cursors to be created
        # and used in a transaction.
        cur = await conn.cursor(
            "SELECT * FROM users WHERE dob > $1", datetime.date(1970, 1, 1)
        )
        record = await cur.fetchrow()
        print(record)
        while await cur.forward(1):
            record = await cur.fetchrow()
            print(record)

    await conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "BEGIN;", "type": "default"},
        {
            "category": "query",
            "data": {"db.cursor": mock.ANY},
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {"db.cursor": mock.ANY},
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "COMMIT;", "type": "default"},
    ]


@pytest.mark.asyncio
async def test_prepared_stmt(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.executemany(
        "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
        [
            ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
            ("Alice", "pw", datetime.date(1990, 12, 25)),
        ],
    )

    stmt = await conn.prepare("SELECT * FROM users WHERE name = $1")

    print(await stmt.fetchval("Bob"))
    print(await stmt.fetchval("Alice"))

    await conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        CRUMBS_CONNECT,
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT * FROM users WHERE name = $1",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
async def test_connection_pool(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    pool_size = 2

    pool = await asyncpg.create_pool(
        PG_CONNECTION_URI, min_size=pool_size, max_size=pool_size
    )

    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "Bob",
            "secret_pw",
            datetime.date(1984, 3, 1),
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE name = $1", "Bob")
        assert row == (1, "Bob", "secret_pw", datetime.date(1984, 3, 1))

    await pool.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [
        # The connection pool opens pool_size connections so we have the crumbs pool_size times
        *[CRUMBS_CONNECT] * pool_size,
        {
            "category": "query",
            "data": {},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT * FROM users WHERE name = $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {},
            "message": "SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_disabled(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_options = {
        "integrations": [AsyncPGIntegration()],
        "traces_sample_rate": 1.0,
        "enable_db_query_source": False,
        "db_query_source_threshold_ms": 0,
        "_experiments": {
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    }

    sentry_init(**sentry_options)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        insert_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert insert_span["name"].startswith("INSERT INTO")
        assert connect_span["name"] == "connect"
        data = insert_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_enabled(
    sentry_init, capture_events, capture_items, enable_db_query_source, span_streaming
):
    sentry_options = {
        "integrations": [AsyncPGIntegration()],
        "traces_sample_rate": 1.0,
        "db_query_source_threshold_ms": 0,
        "_experiments": {
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    }
    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source

    sentry_init(**sentry_options)

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        insert_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert insert_span["name"].startswith("INSERT INTO")
        assert connect_span["name"] == "connect"
        data = insert_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()

        (event,) = events
        span = event["spans"][-1]
        assert span["description"].startswith("INSERT INTO")
        data = span.get("data", {})

    lineno_key = "code.line.number" if span_streaming else SPANDATA.CODE_LINENO
    filepath_key = "code.file.path" if span_streaming else SPANDATA.CODE_FILEPATH

    assert lineno_key in data
    assert filepath_key in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FUNCTION in data


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        insert_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert insert_span["name"].startswith("INSERT INTO")
        assert connect_span["name"] == "connect"
        data = insert_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

            await conn.close()

        (event,) = events
        span = event["spans"][-1]
        assert span["description"].startswith("INSERT INTO")
        data = span.get("data", {})

    lineno_key = "code.line.number" if span_streaming else SPANDATA.CODE_LINENO
    filepath_key = "code.file.path" if span_streaming else SPANDATA.CODE_FILEPATH

    assert lineno_key in data
    assert filepath_key in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(lineno_key)) == int
    assert data.get(lineno_key) > 0
    assert (
        data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.asyncpg.test_asyncpg"
    )
    assert data.get(filepath_key).endswith("tests/integrations/asyncpg/test_asyncpg.py")

    is_relative_path = data.get(filepath_key)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_query_source"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_with_module_in_search_path(
    sentry_init, capture_events, capture_items, span_streaming
):
    """
    Test that query source is relative to the path of the module it ran in
    """
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    from asyncpg_helpers.helpers import execute_query_in_connection

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await execute_query_in_connection(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                conn,
            )

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        insert_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert insert_span["name"].startswith("INSERT INTO")
        assert connect_span["name"] == "connect"
        data = insert_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await execute_query_in_connection(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                conn,
            )

            await conn.close()

        (event,) = events

        span = event["spans"][-1]
        assert span["description"].startswith("INSERT INTO")
        data = span.get("data", {})

    lineno_key = "code.line.number" if span_streaming else SPANDATA.CODE_LINENO
    filepath_key = "code.file.path" if span_streaming else SPANDATA.CODE_FILEPATH

    assert lineno_key in data
    assert filepath_key in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(lineno_key)) == int
    assert data.get(lineno_key) > 0
    assert data.get(filepath_key) == "asyncpg_helpers/helpers.py"
    assert data.get(SPANDATA.CODE_NAMESPACE) == "asyncpg_helpers.helpers"

    is_relative_path = data.get(filepath_key)[0] != os.sep
    assert is_relative_path

    assert data.get(SPANDATA.CODE_FUNCTION) == "execute_query_in_connection"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_no_query_source_if_duration_too_short(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")

        @contextmanager
        def fake_record_sql_queries_streaming(*args, **kwargs):
            with record_sql_queries_supporting_streaming(*args, **kwargs) as span:
                pass
            span._start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            if span_streaming:
                span._end_timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            else:
                span._timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
            yield span

        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            with mock.patch(
                "sentry_sdk.integrations.asyncpg.record_sql_queries_supporting_streaming",
                fake_record_sql_queries_streaming,
            ):
                await conn.execute(
                    "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                )

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        insert_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert insert_span["name"].startswith("INSERT INTO")
        assert connect_span["name"] == "connect"
        data = insert_span.get("attributes", {})
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            @contextmanager
            def fake_record_sql_queries(*args, **kwargs):
                with record_sql_queries_supporting_streaming(*args, **kwargs) as span:
                    pass
                span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
                span.timestamp = datetime.datetime(2024, 1, 1, microsecond=99999)
                yield span

            with mock.patch(
                "sentry_sdk.integrations.asyncpg.record_sql_queries_supporting_streaming",
                fake_record_sql_queries,
            ):
                await conn.execute(
                    "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
                )

            await conn.close()

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
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    events = capture_events()

    with start_transaction(name="test_transaction", sampled=True):
        conn: Connection = await connect(PG_CONNECTION_URI)

        @contextmanager
        def fake_record_sql_queries(*args, **kwargs):
            with record_sql_queries_supporting_streaming(*args, **kwargs) as span:
                pass
            span.start_timestamp = datetime.datetime(2024, 1, 1, microsecond=0)
            span.timestamp = datetime.datetime(2024, 1, 1, microsecond=100001)
            yield span

        with mock.patch(
            "sentry_sdk.integrations.asyncpg.record_sql_queries_supporting_streaming",
            fake_record_sql_queries,
        ):
            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES ('Alice', 'secret', '1990-12-25')",
            )

        await conn.close()

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
        data.get(SPANDATA.CODE_NAMESPACE) == "tests.integrations.asyncpg.test_asyncpg"
    )
    assert data.get(SPANDATA.CODE_FILEPATH).endswith(
        "tests/integrations/asyncpg/test_asyncpg.py"
    )

    is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
    assert is_relative_path

    assert (
        data.get(SPANDATA.CODE_FUNCTION)
        == "test_query_source_if_duration_over_threshold"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_span_origin(sentry_init, capture_events, capture_items, span_streaming):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute("SELECT 1")
            await conn.fetchrow("SELECT 2")
            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 4

        connect_span = spans[0]
        select1_span = spans[1]
        select2_span = spans[2]
        segment = spans[3]

        assert segment["name"] == "test_segment"
        assert connect_span["name"] == "connect"
        assert select1_span["name"] == "SELECT 1"
        assert select2_span["name"] == "SELECT 2"

        assert segment["attributes"]["sentry.origin"] == "manual"
        assert connect_span["attributes"]["sentry.origin"] == "auto.db.asyncpg"
        assert select1_span["attributes"]["sentry.origin"] == "auto.db.asyncpg"
        assert select2_span["attributes"]["sentry.origin"] == "auto.db.asyncpg"
    else:
        events = capture_events()

        with start_transaction(name="test_transaction"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.execute("SELECT 1")
            await conn.fetchrow("SELECT 2")
            await conn.close()

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "manual"

        for span in event["spans"]:
            assert span["origin"] == "auto.db.asyncpg"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_multiline_query_description_normalized(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.execute(
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
            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 3

        connect_span = spans[0]
        select_span = spans[1]
        segment = spans[2]

        assert segment["name"] == "test_segment"
        assert connect_span["name"] == "connect"
        assert select_span["name"] == "SELECT id, name FROM users WHERE name = 'Alice'"
    else:
        events = capture_events()

        with start_transaction(name="test_transaction"):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.execute(
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
            await conn.close()

        (event,) = events

        spans = [
            s
            for s in event["spans"]
            if s["op"] == "db" and "SELECT" in s.get("description", "")
        ]
        assert len(spans) == 1
        assert (
            spans[0]["description"] == "SELECT id, name FROM users WHERE name = 'Alice'"
        )


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
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        before_send_transaction=before_send_transaction,
    )
    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn: Connection = await connect(PG_CONNECTION_URI)
        await conn.execute(
            """
            SELECT
                id,
                name
            FROM
                users
            """
        )
        await conn.close()

    (event,) = events
    spans = [
        s
        for s in event["spans"]
        if s["op"] == "db" and "filtered" in s.get("description", "")
    ]

    assert len(spans) == 1
    assert spans[0]["description"] == "filtered"


def _assert_query_source(span, span_streaming, expected_function):
    if span_streaming:
        data = span.get("attributes", {})
        lineno_key = "code.line.number"
        filepath_key = "code.file.path"
    else:
        data = span.get("data", {})
        lineno_key = SPANDATA.CODE_LINENO
        filepath_key = SPANDATA.CODE_FILEPATH

    assert lineno_key in data
    assert filepath_key in data
    assert SPANDATA.CODE_NAMESPACE in data
    assert SPANDATA.CODE_FUNCTION in data

    assert type(data.get(lineno_key)) == int
    assert data.get(lineno_key) > 0
    assert data[SPANDATA.CODE_NAMESPACE] == "tests.integrations.asyncpg.test_asyncpg"
    assert data.get(filepath_key).endswith("tests/integrations/asyncpg/test_asyncpg.py")
    assert data.get(filepath_key)[0] != os.sep
    assert data[SPANDATA.CODE_FUNCTION] == expected_function


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_execute(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                "Alice",
                "pw",
                datetime.date(1990, 12, 25),
            )
            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]
        assert len(spans) == 3

        connect_span = spans[0]
        query_span = spans[1]
        segment = spans[2]

        assert connect_span["name"] == "connect"
        assert query_span["name"].startswith("INSERT INTO")
        assert segment["name"] == "test_segment"
        assert segment["is_segment"] is True
    else:
        events = capture_events()
        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.execute(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                "Alice",
                "pw",
                datetime.date(1990, 12, 25),
            )
            await conn.close()

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 2
        assert spans[0]["description"] == "connect"
        assert spans[1]["description"].startswith("INSERT INTO")
        query_span = spans[1]

    _assert_query_source(query_span, span_streaming, "test_query_source_execute")


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_executemany(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.executemany(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                [("Bob", "secret_pw", datetime.date(1984, 3, 1))],
            )
            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]
        assert len(spans) == 3

        connect_span = spans[0]
        query_span = spans[1]
        segment = spans[2]

        assert connect_span["name"] == "connect"
        assert query_span["name"].startswith("INSERT INTO")
        assert segment["name"] == "test_segment"
    else:
        events = capture_events()
        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.executemany(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                [("Bob", "secret_pw", datetime.date(1984, 3, 1))],
            )
            await conn.close()

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 2
        assert spans[0]["description"] == "connect"
        assert spans[1]["description"].startswith("INSERT INTO")
        query_span = spans[1]

    _assert_query_source(query_span, span_streaming, "test_query_source_executemany")


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_query_source_prepare(
    sentry_init, capture_events, capture_items, span_streaming
):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.prepare("SELECT * FROM users WHERE name = $1")
            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]
        assert len(spans) == 3
        connect_span = spans[0]
        query_span = spans[1]
        segment = spans[2]

        assert connect_span["name"] == "connect"
        assert query_span["name"] == "SELECT * FROM users WHERE name = $1"
        assert segment["name"] == "test_segment"
    else:
        events = capture_events()
        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)
            await conn.prepare("SELECT * FROM users WHERE name = $1")
            await conn.close()

        (event,) = events
        spans = event["spans"]
        assert len(spans) == 2
        assert spans[0]["description"] == "connect"
        assert spans[1]["description"] == "SELECT * FROM users WHERE name = $1"
        query_span = spans[1]

    _assert_query_source(query_span, span_streaming, "test_query_source_prepare")


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_cursor__bind_exec_creates_spans(
    sentry_init, capture_events, capture_items, span_streaming
) -> None:
    """
    Exercises the bind_exec patch through the iterator that's created in asyncpg when "for record in conn.cursor" is called.
    See https://github.com/MagicStack/asyncpg/blob/db8ecc2a38e16fb0c090aef6f5506547c2831c24/asyncpg/cursor.py#L234
    """
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        _experiments={
            "trace_lifecycle": "stream" if span_streaming else "static",
        },
    )

    if span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="test_segment"):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.executemany(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                [
                    ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
                    ("Alice", "pw", datetime.date(1990, 12, 25)),
                ],
            )

            async with conn.transaction():
                async for record in conn.cursor(
                    "SELECT * FROM users WHERE dob > $1",
                    datetime.date(1970, 1, 1),
                ):
                    pass

            await conn.close()
        sentry_sdk.flush()

        spans = [item.payload for item in items]

        assert len(spans) == 6

        connect_span = spans[0]
        executemany_span = spans[1]
        begin_span = spans[2]
        bind_exec_span = spans[3]
        commit_span = spans[4]
        segment = spans[5]

        assert connect_span["name"] == "connect"
        assert (
            executemany_span["name"]
            == "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)"
        )
        assert begin_span["name"] == "BEGIN;"
        assert bind_exec_span["name"] == "SELECT * FROM users WHERE dob > $1"
        assert commit_span["name"] == "COMMIT;"
        assert segment["name"] == "test_segment"

        assert bind_exec_span["attributes"]["sentry.origin"] == "auto.db.asyncpg"
        assert bind_exec_span["attributes"]["sentry.op"] == "db"
        assert bind_exec_span["attributes"]["db.system"] == "postgresql"
        assert bind_exec_span["attributes"]["db.driver.name"] == "asyncpg"
        assert bind_exec_span["attributes"]["server.address"] == PG_HOST
        assert bind_exec_span["attributes"]["server.port"] == PG_PORT
        assert bind_exec_span["attributes"]["db.name"] == PG_NAME
        assert bind_exec_span["attributes"]["db.user"] == PG_USER
    else:
        events = capture_events()

        with start_transaction(name="test_transaction", sampled=True):
            conn: Connection = await connect(PG_CONNECTION_URI)

            await conn.executemany(
                "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
                [
                    ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
                    ("Alice", "pw", datetime.date(1990, 12, 25)),
                ],
            )

            async with conn.transaction():
                async for record in conn.cursor(
                    "SELECT * FROM users WHERE dob > $1",
                    datetime.date(1970, 1, 1),
                ):
                    pass

            await conn.close()

        (event,) = events

        assert len(event["spans"]) == 5

        connect_span = event["spans"][0]
        executemany_span = event["spans"][1]
        begin_span = event["spans"][2]
        bind_exec_span = event["spans"][3]
        commit_span = event["spans"][4]

        assert connect_span["description"] == "connect"
        assert (
            executemany_span["description"]
            == "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)"
        )
        assert begin_span["description"] == "BEGIN;"
        assert bind_exec_span["description"] == "SELECT * FROM users WHERE dob > $1"
        assert commit_span["description"] == "COMMIT;"

        assert bind_exec_span["origin"] == "auto.db.asyncpg"
        assert bind_exec_span["data"]["db.system"] == "postgresql"
        assert bind_exec_span["data"]["db.driver.name"] == "asyncpg"
        assert bind_exec_span["data"]["server.address"] == PG_HOST
        assert bind_exec_span["data"]["server.port"] == PG_PORT
        assert bind_exec_span["data"]["db.name"] == PG_NAME
        assert bind_exec_span["data"]["db.user"] == PG_USER

    _assert_query_source(
        bind_exec_span,
        span_streaming,
        "test_cursor__bind_exec_creates_spans",
    )


@pytest.mark.asyncio
async def test_cursor__exec_methods_create_spans(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )
    events = capture_events()

    with start_transaction(name="test_transaction"):
        conn: Connection = await connect(PG_CONNECTION_URI)

        await conn.executemany(
            "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            [
                ("Bob", "secret_pw", datetime.date(1984, 3, 1)),
                ("Alice", "pw", datetime.date(1990, 12, 25)),
            ],
        )

        async with conn.transaction():
            cur = await conn.cursor(
                "SELECT * FROM users WHERE dob > $1", datetime.date(1970, 1, 1)
            )
            # These exercise the `_exec` patch
            await cur.fetchrow()
            await cur.fetchrow()

        await conn.close()

    (event,) = events

    assert len(event["spans"]) == 6

    connect_span = event["spans"][0]
    executemany_span = event["spans"][1]
    begin_span = event["spans"][2]
    fetchrow_span_1 = event["spans"][3]
    fetchrow_span_2 = event["spans"][4]
    commit_span = event["spans"][5]

    assert connect_span["description"] == "connect"
    assert (
        executemany_span["description"]
        == "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)"
    )
    assert begin_span["description"] == "BEGIN;"
    assert fetchrow_span_1["description"] == "SELECT * FROM users WHERE dob > $1"
    assert fetchrow_span_2["description"] == "SELECT * FROM users WHERE dob > $1"
    assert commit_span["description"] == "COMMIT;"

    for span in (fetchrow_span_1, fetchrow_span_2):
        assert span["data"]["db.cursor"] is not None
        assert span["data"]["db.system"] == "postgresql"
        assert span["data"]["db.driver.name"] == "asyncpg"
        assert span["origin"] == "auto.db.asyncpg"
        _assert_query_source(
            span,
            False,
            "test_cursor__exec_methods_create_spans",
        )
