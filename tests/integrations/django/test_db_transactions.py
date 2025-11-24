import os
import pytest
import itertools
from datetime import datetime

from django.db import connections
from django.contrib.auth.models import User

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from werkzeug.test import Client

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA, SPANNAME
from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.django.myapp.wsgi import application


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_transaction_spans_disabled_no_autocommit(
    sentry_init, client, capture_events
):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_no_autocommit_rollback"))
    client.get(reverse("postgres_insert_orm_no_autocommit"))

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        cursor = connection.cursor()

        query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

        query_list = (
            (
                "user1",
                "John",
                "Doe",
                "user1@example.com",
                datetime(1970, 1, 1),
            ),
            (
                "user2",
                "Max",
                "Mustermann",
                "user2@example.com",
                datetime(1970, 1, 1),
            ),
        )

        transaction.set_autocommit(False)
        cursor.executemany(query, query_list)
        transaction.rollback()
        transaction.set_autocommit(True)

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        cursor = connection.cursor()

        query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

        query_list = (
            (
                "user1",
                "John",
                "Doe",
                "user1@example.com",
                datetime(1970, 1, 1),
            ),
            (
                "user2",
                "Max",
                "Mustermann",
                "user2@example.com",
                datetime(1970, 1, 1),
            ),
        )

        transaction.set_autocommit(False)
        cursor.executemany(query, query_list)
        transaction.commit()
        transaction.set_autocommit(True)

    (postgres_rollback, postgres_commit, sqlite_rollback, sqlite_commit) = events

    # Ensure operation is persisted
    assert User.objects.using("postgres").exists()

    assert postgres_rollback["contexts"]["trace"]["origin"] == "auto.http.django"
    assert postgres_commit["contexts"]["trace"]["origin"] == "auto.http.django"
    assert sqlite_rollback["contexts"]["trace"]["origin"] == "manual"
    assert sqlite_commit["contexts"]["trace"]["origin"] == "manual"

    commit_spans = [
        span
        for span in itertools.chain(
            postgres_rollback["spans"],
            postgres_commit["spans"],
            sqlite_rollback["spans"],
            sqlite_commit["spans"],
        )
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
        or span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(commit_spans) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_transaction_spans_disabled_atomic(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_atomic_rollback"))
    client.get(reverse("postgres_insert_orm_atomic"))

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        with transaction.atomic():
            cursor = connection.cursor()

            query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

            query_list = (
                (
                    "user1",
                    "John",
                    "Doe",
                    "user1@example.com",
                    datetime(1970, 1, 1),
                ),
                (
                    "user2",
                    "Max",
                    "Mustermann",
                    "user2@example.com",
                    datetime(1970, 1, 1),
                ),
            )
            cursor.executemany(query, query_list)
            transaction.set_rollback(True)

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        with transaction.atomic():
            cursor = connection.cursor()

            query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

            query_list = (
                (
                    "user1",
                    "John",
                    "Doe",
                    "user1@example.com",
                    datetime(1970, 1, 1),
                ),
                (
                    "user2",
                    "Max",
                    "Mustermann",
                    "user2@example.com",
                    datetime(1970, 1, 1),
                ),
            )
            cursor.executemany(query, query_list)

    (postgres_rollback, postgres_commit, sqlite_rollback, sqlite_commit) = events

    # Ensure operation is persisted
    assert User.objects.using("postgres").exists()

    assert postgres_rollback["contexts"]["trace"]["origin"] == "auto.http.django"
    assert postgres_commit["contexts"]["trace"]["origin"] == "auto.http.django"
    assert sqlite_rollback["contexts"]["trace"]["origin"] == "manual"
    assert sqlite_commit["contexts"]["trace"]["origin"] == "manual"

    commit_spans = [
        span
        for span in itertools.chain(
            postgres_rollback["spans"],
            postgres_commit["spans"],
            sqlite_rollback["spans"],
            sqlite_commit["spans"],
        )
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
        or span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(commit_spans) == 0


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_no_autocommit_execute(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_no_autocommit"))

    (event,) = events

    # Ensure operation is persisted
    assert User.objects.using("postgres").exists()

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    commit_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
    ]
    assert len(commit_spans) == 1
    commit_span = commit_spans[0]
    assert commit_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert commit_span["data"].get(SPANDATA.DB_SYSTEM) == "postgresql"
    conn_params = connections["postgres"].get_connection_params()
    assert commit_span["data"].get(SPANDATA.DB_NAME) is not None
    assert commit_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")
    assert commit_span["data"].get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
    )
    assert commit_span["data"].get(SPANDATA.SERVER_PORT) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
    )

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]
    assert len(insert_spans) == 1
    insert_span = insert_spans[0]

    # Verify query and commit statements are siblings
    assert commit_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_no_autocommit_executemany(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        cursor = connection.cursor()

        query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

        query_list = (
            (
                "user1",
                "John",
                "Doe",
                "user1@example.com",
                datetime(1970, 1, 1),
            ),
            (
                "user2",
                "Max",
                "Mustermann",
                "user2@example.com",
                datetime(1970, 1, 1),
            ),
        )

        transaction.set_autocommit(False)
        cursor.executemany(query, query_list)
        transaction.commit()
        transaction.set_autocommit(True)

    (event,) = events

    # Ensure operation is persisted
    assert User.objects.exists()

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.django"

    commit_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
    ]
    assert len(commit_spans) == 1
    commit_span = commit_spans[0]
    assert commit_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert commit_span["data"].get(SPANDATA.DB_SYSTEM) == "sqlite"
    conn_params = connection.get_connection_params()
    assert commit_span["data"].get(SPANDATA.DB_NAME) is not None
    assert commit_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]

    # Verify queries and commit statements are siblings
    for insert_span in insert_spans:
        assert commit_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_no_autocommit_rollback_execute(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_no_autocommit_rollback"))

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.using("postgres").exists()

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "postgresql"
    conn_params = connections["postgres"].get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")
    assert rollback_span["data"].get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
    )
    assert rollback_span["data"].get(SPANDATA.SERVER_PORT) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
    )

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]
    assert len(insert_spans) == 1
    insert_span = insert_spans[0]

    # Verify query and rollback statements are siblings
    assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_no_autocommit_rollback_executemany(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        cursor = connection.cursor()

        query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

        query_list = (
            (
                "user1",
                "John",
                "Doe",
                "user1@example.com",
                datetime(1970, 1, 1),
            ),
            (
                "user2",
                "Max",
                "Mustermann",
                "user2@example.com",
                datetime(1970, 1, 1),
            ),
        )

        transaction.set_autocommit(False)
        cursor.executemany(query, query_list)
        transaction.rollback()
        transaction.set_autocommit(True)

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.exists()

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.django"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "sqlite"
    conn_params = connection.get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]

    # Verify queries and rollback statements are siblings
    for insert_span in insert_spans:
        assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_execute(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_atomic"))

    (event,) = events

    # Ensure operation is persisted
    assert User.objects.using("postgres").exists()

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    commit_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
    ]
    assert len(commit_spans) == 1
    commit_span = commit_spans[0]
    assert commit_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert commit_span["data"].get(SPANDATA.DB_SYSTEM) == "postgresql"
    conn_params = connections["postgres"].get_connection_params()
    assert commit_span["data"].get(SPANDATA.DB_NAME) is not None
    assert commit_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")
    assert commit_span["data"].get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
    )
    assert commit_span["data"].get(SPANDATA.SERVER_PORT) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
    )

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]
    assert len(insert_spans) == 1
    insert_span = insert_spans[0]

    # Verify query and commit statements are siblings
    assert commit_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_executemany(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        with transaction.atomic():
            cursor = connection.cursor()

            query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

            query_list = (
                (
                    "user1",
                    "John",
                    "Doe",
                    "user1@example.com",
                    datetime(1970, 1, 1),
                ),
                (
                    "user2",
                    "Max",
                    "Mustermann",
                    "user2@example.com",
                    datetime(1970, 1, 1),
                ),
            )
            cursor.executemany(query, query_list)

    (event,) = events

    # Ensure operation is persisted
    assert User.objects.exists()

    assert event["contexts"]["trace"]["origin"] == "manual"

    commit_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_COMMIT
    ]
    assert len(commit_spans) == 1
    commit_span = commit_spans[0]
    assert commit_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert commit_span["data"].get(SPANDATA.DB_SYSTEM) == "sqlite"
    conn_params = connection.get_connection_params()
    assert commit_span["data"].get(SPANDATA.DB_NAME) is not None
    assert commit_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]

    # Verify queries and commit statements are siblings
    for insert_span in insert_spans:
        assert commit_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_rollback_execute(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_atomic_rollback"))

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.using("postgres").exists()

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "postgresql"
    conn_params = connections["postgres"].get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")
    assert rollback_span["data"].get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
    )
    assert rollback_span["data"].get(SPANDATA.SERVER_PORT) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
    )

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]
    assert len(insert_spans) == 1
    insert_span = insert_spans[0]

    # Verify query and rollback statements are siblings
    assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_rollback_executemany(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        with transaction.atomic():
            cursor = connection.cursor()

            query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

            query_list = (
                (
                    "user1",
                    "John",
                    "Doe",
                    "user1@example.com",
                    datetime(1970, 1, 1),
                ),
                (
                    "user2",
                    "Max",
                    "Mustermann",
                    "user2@example.com",
                    datetime(1970, 1, 1),
                ),
            )
            cursor.executemany(query, query_list)
            transaction.set_rollback(True)

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.exists()

    assert event["contexts"]["trace"]["origin"] == "manual"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "sqlite"
    conn_params = connection.get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]

    # Verify queries and rollback statements are siblings
    for insert_span in insert_spans:
        assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_execute_exception(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_insert_orm_atomic_exception"))

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.using("postgres").exists()

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "postgresql"
    conn_params = connections["postgres"].get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")
    assert rollback_span["data"].get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
    )
    assert rollback_span["data"].get(SPANDATA.SERVER_PORT) == os.environ.get(
        "SENTRY_PYTHON_TEST_POSTGRES_PORT", "5432"
    )

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]
    assert len(insert_spans) == 1
    insert_span = insert_spans[0]

    # Verify query and rollback statements are siblings
    assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_atomic_executemany_exception(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration(db_transaction_spans=True)],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        try:
            with transaction.atomic():
                cursor = connection.cursor()

                query = """INSERT INTO auth_user (
    password,
    is_superuser,
    username,
    first_name,
    last_name,
    email,
    is_staff,
    is_active,
    date_joined
)
VALUES ('password', false, %s, %s, %s, %s, false, true, %s);"""

                query_list = (
                    (
                        "user1",
                        "John",
                        "Doe",
                        "user1@example.com",
                        datetime(1970, 1, 1),
                    ),
                    (
                        "user2",
                        "Max",
                        "Mustermann",
                        "user2@example.com",
                        datetime(1970, 1, 1),
                    ),
                )
                cursor.executemany(query, query_list)
                1 / 0
        except ZeroDivisionError:
            pass

    (event,) = events

    # Ensure operation is rolled back
    assert not User.objects.exists()

    assert event["contexts"]["trace"]["origin"] == "manual"

    rollback_spans = [
        span
        for span in event["spans"]
        if span["data"].get(SPANDATA.DB_OPERATION) == SPANNAME.DB_ROLLBACK
    ]
    assert len(rollback_spans) == 1
    rollback_span = rollback_spans[0]
    assert rollback_span["origin"] == "auto.db.django"

    # Verify other database attributes
    assert rollback_span["data"].get(SPANDATA.DB_SYSTEM) == "sqlite"
    conn_params = connection.get_connection_params()
    assert rollback_span["data"].get(SPANDATA.DB_NAME) is not None
    assert rollback_span["data"].get(SPANDATA.DB_NAME) == conn_params.get(
        "database"
    ) or conn_params.get("dbname")

    insert_spans = [
        span for span in event["spans"] if span["description"].startswith("INSERT INTO")
    ]

    # Verify queries and rollback statements are siblings
    for insert_span in insert_spans:
        assert rollback_span["parent_span_id"] == insert_span["parent_span_id"]
