"""
Tests need pytest-asyncio installed.

Tests need a local postgresql instance running, this can best be done using
```sh
docker run --rm --name some-postgres -e POSTGRES_USER=foo -e POSTGRES_PASSWORD=bar -d -p 5432:5432 postgres
```

The tests use the following credentials to establish a database connection.
"""
import os


PG_NAME = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_NAME", "postgres")
PG_USER = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_USER", "foo")
PG_PASSWORD = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_PASSWORD", "bar")
PG_HOST = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost")
PG_PORT = 5432


import datetime

import asyncpg
import pytest

import pytest_asyncio

from asyncpg import connect, Connection

from sentry_sdk import capture_message, start_transaction
from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
from sentry_sdk.consts import SPANDATA


PG_CONNECTION_URI = "postgresql://{}:{}@{}/{}".format(
    PG_USER, PG_PASSWORD, PG_HOST, PG_NAME
)
CRUMBS_CONNECT = {
    "category": "query",
    "data": {
        "db.name": PG_NAME,
        "db.system": "postgresql",
        "db.user": PG_USER,
        "server.address": PG_HOST,
        "server.port": PG_PORT,
    },
    "message": "connect",
    "type": "default",
}


@pytest_asyncio.fixture(autouse=True)
async def _clean_pg():
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
async def test_connect(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
    events = capture_events()

    conn: Connection = await connect(PG_CONNECTION_URI)

    await conn.close()

    capture_message("hi")

    (event,) = events

    for crumb in event["breadcrumbs"]["values"]:
        del crumb["timestamp"]

    assert event["breadcrumbs"]["values"] == [CRUMBS_CONNECT]


@pytest.mark.asyncio
async def test_execute(sentry_init, capture_events) -> None:
    sentry_init(
        integrations=[AsyncPGIntegration()],
        _experiments={"record_sql_params": True},
    )
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
async def test_execute_many(sentry_init, capture_events) -> None:
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
            "data": {},
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
            "data": {},
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
            "message": "SELECT pg_advisory_unlock_all();\n"
            "CLOSE ALL;\n"
            "UNLISTEN *;\n"
            "RESET ALL;",
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
            "message": "SELECT pg_advisory_unlock_all();\n"
            "CLOSE ALL;\n"
            "UNLISTEN *;\n"
            "RESET ALL;",
            "type": "default",
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_db_query_source", [None, False])
async def test_query_source_disabled(
    sentry_init, capture_events, enable_db_query_source
):
    sentry_options = {
        "integrations": [AsyncPGIntegration()],
        "enable_tracing": True,
    }
    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source
        sentry_options["db_query_source_threshold_ms"] = 0

    sentry_init(**sentry_options)

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
async def test_query_source(sentry_init, capture_events):
    sentry_init(
        integrations=[AsyncPGIntegration()],
        enable_tracing=True,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )

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

    assert data.get(SPANDATA.CODE_FUNCTION) == "test_query_source"
