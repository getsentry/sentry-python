"""
Tests need pytest-asyncio installed.

Tests need a local postgresql instance running, this can best be done using
```sh
docker run --rm --name some-postgres -e POSTGRES_USER=foo -e POSTGRES_PASSWORD=bar -d -p 5432:5432 postgres
```

The tests use the following credentials to establish a database connection.
"""
import os
import re

PG_NAME = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_NAME", "postgres")
PG_USER = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_USER", "foo")
PG_PASSWORD = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_PASSWORD", "bar")
PG_HOST = os.getenv("SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost")
PG_PORT = 5432


import datetime

import asyncpg
import pytest
from asyncpg import connect, Connection

from sentry_sdk import capture_message
from sentry_sdk.integrations.asyncpg import AsyncPGIntegration
from tests.integrations.asgi import pytest_asyncio


PG_CONNECTION_URI = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}/{PG_NAME}"


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

    assert event["breadcrumbs"]["values"] != []


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
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        }
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
        {
            "category": "query",
            "data": {
                "db.params": ["Bob", "secret_pw", "datetime.date(1984, 3, 1)"],
                "db.paramstyle": "format",
            },
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        }
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
    #
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
        if "db.cursor" in crumb["data"]:
            # asyncpg cursor's __repr__ adds the memory address - replace for testing purposes
            crumb["data"]["db.cursor"] = re.sub(
                r"0x.+>$", "0xdeadbeef1234>", crumb["data"]["db.cursor"]
            )

    assert event["breadcrumbs"]["values"] == [
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "BEGIN;", "type": "default"},
        {
            "category": "query",
            "data": {
                "db.cursor": '<asyncpg.CursorIterator "SELECT * FROM users WHERE dob " '
                "exhausted 0xdeadbeef1234>"
            },
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.cursor": '<asyncpg.CursorIterator "SELECT * FROM users WHERE dob " '
                "exhausted 0xdeadbeef1234>"
            },
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.cursor": '<asyncpg.CursorIterator "SELECT * FROM users WHERE dob " '
                "exhausted 0xdeadbeef1234>",
                "db.cursor.exhausted": True,
            },
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
        if "db.cursor" in crumb["data"]:
            # asyncpg cursor's __repr__ adds the memory address - replace for testing purposes
            crumb["data"]["db.cursor"] = re.sub(
                r"0x.+>$", "0xdeadbeef1234>", crumb["data"]["db.cursor"]
            )

    assert event["breadcrumbs"]["values"] == [
        {
            "category": "query",
            "data": {"db.executemany": True},
            "message": "INSERT INTO users(name, password, dob) VALUES($1, $2, $3)",
            "type": "default",
        },
        {"category": "query", "data": {}, "message": "BEGIN;", "type": "default"},
        {
            "category": "query",
            "data": {
                "db.cursor": '<asyncpg.Cursor "SELECT * FROM users WHERE dob " '
                "exhausted 0xdeadbeef1234>"
            },
            "message": "SELECT * FROM users WHERE dob > $1",
            "type": "default",
        },
        {
            "category": "query",
            "data": {
                "db.cursor": '<asyncpg.Cursor "SELECT * FROM users WHERE dob " '
                "exhausted 0xdeadbeef1234>"
            },
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

    pool = await asyncpg.create_pool(PG_CONNECTION_URI)

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
