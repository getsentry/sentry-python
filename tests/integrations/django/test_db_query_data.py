import os

import pytest
from datetime import datetime
from unittest import mock

from django import VERSION as DJANGO_VERSION
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from werkzeug.test import Client

from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import (
    DjangoIntegration,
    _set_db_data,
    _cached_db_configs,
)
from sentry_sdk.tracing_utils import record_sql_queries

from tests.conftest import unpack_werkzeug_response
from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.django.myapp.wsgi import application


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source_disabled(sentry_init, client, capture_events):
    sentry_options = {
        "integrations": [DjangoIntegration()],
        "send_default_pii": True,
        "traces_sample_rate": 1.0,
        "enable_db_query_source": False,
        "db_query_source_threshold_ms": 0,
    }

    sentry_init(**sentry_options)

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(client.get(reverse("postgres_select_orm")))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data
            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
@pytest.mark.parametrize("enable_db_query_source", [None, True])
def test_query_source_enabled(
    sentry_init, client, capture_events, enable_db_query_source
):
    sentry_options = {
        "integrations": [DjangoIntegration()],
        "send_default_pii": True,
        "traces_sample_rate": 1.0,
        "db_query_source_threshold_ms": 0,
    }

    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source

    sentry_init(**sentry_options)

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(client.get(reverse("postgres_select_orm")))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(client.get(reverse("postgres_select_orm")))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0

            assert (
                data.get(SPANDATA.CODE_NAMESPACE)
                == "tests.integrations.django.myapp.views"
            )
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "tests/integrations/django/myapp/views.py"
            )

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert data.get(SPANDATA.CODE_FUNCTION) == "postgres_select_orm"

            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source_with_module_in_search_path(sentry_init, client, capture_events):
    """
    Test that query source is relative to the path of the module it ran in
    """
    client = Client(application)

    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(
        client.get(reverse("postgres_select_slow_from_supplement"))
    )
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0
            assert data.get(SPANDATA.CODE_NAMESPACE) == "django_helpers.views"
            assert data.get(SPANDATA.CODE_FILEPATH) == "django_helpers/views.py"

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert data.get(SPANDATA.CODE_FUNCTION) == "postgres_select_orm"

            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source_with_in_app_exclude(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        in_app_exclude=["tests.integrations.django.myapp.views"],
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(client.get(reverse("postgres_select_orm")))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0

            if DJANGO_VERSION >= (1, 11):
                assert (
                    data.get(SPANDATA.CODE_NAMESPACE)
                    == "tests.integrations.django.myapp.settings"
                )
                assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                    "tests/integrations/django/myapp/settings.py"
                )
                assert data.get(SPANDATA.CODE_FUNCTION) == "middleware"
            else:
                assert (
                    data.get(SPANDATA.CODE_NAMESPACE)
                    == "tests.integrations.django.test_db_query_data"
                )
                assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                    "tests/integrations/django/test_db_query_data.py"
                )
                assert (
                    data.get(SPANDATA.CODE_FUNCTION)
                    == "test_query_source_with_in_app_exclude"
                )

            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source_with_in_app_include(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=0,
        in_app_include=["django"],
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = unpack_werkzeug_response(client.get(reverse("postgres_select_orm")))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0

            assert data.get(SPANDATA.CODE_NAMESPACE) == "django.db.models.sql.compiler"
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "django/db/models/sql/compiler.py"
            )
            assert data.get(SPANDATA.CODE_FUNCTION) == "execute_sql"
            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_no_query_source_if_duration_too_short(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    class fake_record_sql_queries:  # noqa: N801
        def __init__(self, *args, **kwargs):
            with record_sql_queries(*args, **kwargs) as span:
                self.span = span

            self.span.start_timestamp = datetime(2024, 1, 1, microsecond=0)
            self.span.timestamp = datetime(2024, 1, 1, microsecond=99999)

        def __enter__(self):
            return self.span

        def __exit__(self, type, value, traceback):
            pass

    with mock.patch(
        "sentry_sdk.integrations.django.record_sql_queries",
        fake_record_sql_queries,
    ):
        _, status, _ = unpack_werkzeug_response(
            client.get(reverse("postgres_select_orm"))
        )

    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data

            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source_if_duration_over_threshold(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
        db_query_source_threshold_ms=100,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    class fake_record_sql_queries:  # noqa: N801
        def __init__(self, *args, **kwargs):
            with record_sql_queries(*args, **kwargs) as span:
                self.span = span

            self.span.start_timestamp = datetime(2024, 1, 1, microsecond=0)
            self.span.timestamp = datetime(2024, 1, 1, microsecond=101000)

        def __enter__(self):
            return self.span

        def __exit__(self, type, value, traceback):
            pass

    with mock.patch(
        "sentry_sdk.integrations.django.record_sql_queries",
        fake_record_sql_queries,
    ):
        _, status, _ = unpack_werkzeug_response(
            client.get(reverse("postgres_select_orm"))
        )

    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data", {})

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert type(data.get(SPANDATA.CODE_LINENO)) == int
            assert data.get(SPANDATA.CODE_LINENO) > 0

            assert (
                data.get(SPANDATA.CODE_NAMESPACE)
                == "tests.integrations.django.myapp.views"
            )
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "tests/integrations/django/myapp/views.py"
            )

            is_relative_path = data.get(SPANDATA.CODE_FILEPATH)[0] != os.sep
            assert is_relative_path

            assert data.get(SPANDATA.CODE_FUNCTION) == "postgres_select_orm"
            break
    else:
        raise AssertionError("No db span found")


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_span_origin_execute(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    client.get(reverse("postgres_select_orm"))

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "auto.http.django"

    for span in event["spans"]:
        if span["op"] == "db":
            assert span["origin"] == "auto.db.django"
        else:
            assert span["origin"] == "auto.http.django"


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_db_span_origin_executemany(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    events = capture_events()

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    with start_transaction(name="test_transaction"):
        from django.db import connection, transaction

        cursor = connection.cursor()

        query = """UPDATE auth_user SET username = %s where id = %s;"""
        query_list = (
            (
                "test1",
                1,
            ),
            (
                "test2",
                2,
            ),
        )
        cursor.executemany(query, query_list)

        transaction.commit()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.db.django"


def test_set_db_data_with_cached_config(sentry_init):
    """Test _set_db_data uses cached database configuration when available."""
    sentry_init(integrations=[DjangoIntegration()])

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "test_db"
    mock_db.vendor = "postgresql"

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    _cached_db_configs["test_db"] = {
        "db_name": "test_database",
        "host": "localhost",
        "port": 5432,
        "unix_socket": None,
        "engine": "django.db.backends.postgresql",
        "vendor": "postgresql",
    }

    _set_db_data(span, mock_cursor)

    # Verify span data was set correctly from cache
    expected_calls = [
        mock.call("db.system", "postgresql"),
        mock.call("db.name", "test_database"),
        mock.call("server.address", "localhost"),
        mock.call("server.port", "5432"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list

    # Verify unix_socket was not set (it's None)
    assert mock.call("server.socket.address", None) not in span.set_data.call_args_list


def test_set_db_data_with_cached_config_unix_socket(sentry_init):
    """Test _set_db_data handles unix socket from cached config."""
    sentry_init(integrations=[DjangoIntegration()])

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "test_db"
    del mock_db.vendor  # Remove vendor attribute

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    _cached_db_configs["test_db"] = {
        "db_name": "test_database",
        "host": None,
        "port": None,
        "unix_socket": "/tmp/postgres.sock",
        "engine": "django.db.backends.postgresql",
        "vendor": "postgresql",
    }

    _set_db_data(span, mock_cursor)

    # Verify span data was set correctly from cache
    expected_calls = [
        mock.call("db.name", "test_database"),
        mock.call("server.socket.address", "/tmp/postgres.sock"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list

    # Verify host and port were not set (they're None)
    assert mock.call("server.address", None) not in span.set_data.call_args_list
    assert mock.call("server.port", None) not in span.set_data.call_args_list


def test_set_db_data_fallback_to_connection_params(sentry_init):
    """Test _set_db_data falls back to db.get_connection_params() when cache misses."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "uncached_db"
    mock_db.vendor = "mysql"
    mock_db.get_connection_params.return_value = {
        "database": "fallback_db",
        "host": "mysql.example.com",
        "port": 3306,
        "unix_socket": None,
    }

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    with mock.patch("sentry_sdk.integrations.django.logger") as mock_logger:
        _set_db_data(span, mock_cursor)

    # Verify fallback was used
    mock_db.get_connection_params.assert_called_once()
    mock_logger.debug.assert_called_with(
        "Cached db connection config retrieval failed for %s. Trying db.get_connection_params().",
        "uncached_db",
    )

    # Verify span data was set correctly from connection params
    expected_calls = [
        mock.call("db.system", "mysql"),
        mock.call("db.name", "fallback_db"),
        mock.call("server.address", "mysql.example.com"),
        mock.call("server.port", "3306"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list


def test_set_db_data_fallback_to_connection_params_with_dbname(sentry_init):
    """Test _set_db_data handles 'dbname' key in connection params."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "postgres_db"
    mock_db.vendor = "postgresql"
    mock_db.get_connection_params.return_value = {
        "dbname": "postgres_fallback",  # PostgreSQL uses 'dbname' instead of 'database'
        "host": "postgres.example.com",
        "port": 5432,
        "unix_socket": "/var/run/postgresql/.s.PGSQL.5432",
    }

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    _set_db_data(span, mock_cursor)

    # Verify span data was set correctly, preferring 'dbname' over 'database'
    expected_calls = [
        mock.call("db.system", "postgresql"),
        mock.call("db.name", "postgres_fallback"),
        mock.call("server.address", "postgres.example.com"),
        mock.call("server.port", "5432"),
        mock.call("server.socket.address", "/var/run/postgresql/.s.PGSQL.5432"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list


def test_set_db_data_fallback_to_direct_connection_psycopg2(sentry_init):
    """Test _set_db_data falls back to direct connection access for psycopg2."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "direct_db"
    mock_db.vendor = "postgresql"
    mock_db.get_connection_params.side_effect = ImproperlyConfigured("Config error")

    mock_connection = mock.Mock()
    mock_connection.get_dsn_parameters.return_value = {
        "dbname": "direct_access_db",
        "host": "direct.example.com",
        "port": "5432",
    }

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db
    mock_cursor.connection = mock_connection

    with mock.patch("sentry_sdk.integrations.django.logger") as mock_logger, mock.patch(
        "sentry_sdk.integrations.django.inspect.isroutine", return_value=True
    ):
        _set_db_data(span, mock_cursor)

    # Verify both fallbacks were attempted
    mock_db.get_connection_params.assert_called_once()
    mock_connection.get_dsn_parameters.assert_called_once()

    # Verify logging
    assert mock_logger.debug.call_count == 2
    mock_logger.debug.assert_any_call(
        "Cached db connection config retrieval failed for %s. Trying db.get_connection_params().",
        "direct_db",
    )
    mock_logger.debug.assert_any_call(
        "db.get_connection_params() failed for %s, trying direct connection access",
        "direct_db",
    )

    # Verify span data was set from direct connection access
    expected_calls = [
        mock.call("db.system", "postgresql"),
        mock.call("db.name", "direct_access_db"),
        mock.call("server.address", "direct.example.com"),
        mock.call("server.port", "5432"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list


def test_set_db_data_fallback_to_direct_connection_psycopg3(sentry_init):
    """Test _set_db_data falls back to direct connection access for psycopg3."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "psycopg3_db"
    mock_db.vendor = "postgresql"
    mock_db.get_connection_params.side_effect = AttributeError(
        "No get_connection_params"
    )

    mock_connection_info = mock.Mock()
    mock_connection_info.dbname = "psycopg3_db"
    mock_connection_info.port = 5433
    mock_connection_info.host = "psycopg3.example.com"  # Non-Unix socket host

    mock_connection = mock.Mock()
    mock_connection.info = mock_connection_info
    # Remove get_dsn_parameters to simulate psycopg3
    del mock_connection.get_dsn_parameters

    # mock.Mock cursor with psycopg3-style connection
    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db
    mock_cursor.connection = mock_connection

    with mock.patch(
        "sentry_sdk.integrations.django.inspect.isroutine", return_value=False
    ):
        _set_db_data(span, mock_cursor)

    # Verify span data was set from psycopg3 connection info
    expected_calls = [
        mock.call("db.system", "postgresql"),
        mock.call("db.name", "psycopg3_db"),
        mock.call("server.address", "psycopg3.example.com"),
        mock.call("server.port", "5433"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list


def test_set_db_data_psycopg3_unix_socket_filtered(sentry_init):
    """Test _set_db_data filters out Unix socket paths in psycopg3."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "unix_socket_db"
    mock_db.vendor = "postgresql"
    mock_db.get_connection_params.side_effect = KeyError("Missing key")

    mock_connection_info = mock.Mock()
    mock_connection_info.dbname = "unix_socket_db"
    mock_connection_info.port = 5432
    mock_connection_info.host = (
        "/var/run/postgresql"  # Unix socket path starting with /
    )

    mock_connection = mock.Mock()
    mock_connection.info = mock_connection_info
    del mock_connection.get_dsn_parameters

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db
    mock_cursor.connection = mock_connection

    with mock.patch(
        "sentry_sdk.integrations.django.inspect.isroutine", return_value=False
    ):
        _set_db_data(span, mock_cursor)

    # Verify span data was set but host was filtered out (Unix socket)
    expected_calls = [
        mock.call("db.system", "postgresql"),
        mock.call("db.name", "unix_socket_db"),
        mock.call("server.port", "5432"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list

    # Verify host was NOT set (Unix socket path filtered out)
    assert (
        mock.call("server.address", "/var/run/postgresql")
        not in span.set_data.call_args_list
    )


def test_set_db_data_no_alias_db(sentry_init):
    """Test _set_db_data handles database without alias attribute."""
    sentry_init(integrations=[DjangoIntegration()])

    # Clear cache
    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    del mock_db.alias  # Remove alias attribute
    mock_db.vendor = "sqlite"
    mock_db.get_connection_params.return_value = {"database": "test.db"}

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    _set_db_data(span, mock_cursor)

    # Verify it worked despite no alias
    expected_calls = [
        mock.call("db.system", "sqlite"),
        mock.call("db.name", "test.db"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list


def test_set_db_data_direct_db_object(sentry_init):
    """Test _set_db_data handles direct database object (not cursor)."""
    sentry_init(integrations=[DjangoIntegration()])

    # Clear cache
    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.configure_mock(alias="direct_db", vendor="oracle")
    mock_db.get_connection_params.return_value = {
        "database": "orcl",
        "host": "oracle.example.com",
        "port": 1521,
    }

    _set_db_data(span, mock_db)

    # Verify it handled direct db object correctly by checking that span.set_data was called
    assert span.set_data.called
    call_args_list = span.set_data.call_args_list
    assert len(call_args_list) >= 1  # At least db.system should be called

    # Extract the keys that were called
    call_keys = [call[0][0] for call in call_args_list]

    assert "db.system" in call_keys


def test_set_db_data_exception_handling(sentry_init):
    """Test _set_db_data handles exceptions gracefully."""
    sentry_init(integrations=[DjangoIntegration()])

    _cached_db_configs.clear()

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "error_db"
    mock_db.vendor = "postgresql"
    mock_db.get_connection_params.side_effect = Exception("Database error")

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db
    mock_cursor.connection.get_dsn_parameters.side_effect = Exception(
        "Connection error"
    )

    with mock.patch("sentry_sdk.integrations.django.logger") as mock_logger:
        _set_db_data(span, mock_cursor)

    # Verify only vendor was set (from initial db.vendor access)
    expected_calls = [
        mock.call("db.system", "postgresql"),
    ]

    for call in expected_calls:
        assert call in span.set_data.call_args_list

    mock_logger.debug.assert_called_with(
        "Failed to get database connection params for %s: %s", "error_db", mock.ANY
    )


def test_set_db_data_empty_cached_values(sentry_init):
    """Test _set_db_data handles empty/None values in cached config."""
    sentry_init(integrations=[DjangoIntegration()])

    span = mock.Mock()
    span.set_data = mock.Mock()

    mock_db = mock.Mock()
    mock_db.alias = "empty_config_db"
    mock_db.vendor = "postgresql"

    mock_cursor = mock.Mock()
    mock_cursor.db = mock_db

    _cached_db_configs["empty_config_db"] = {
        "db_name": None,  # Should not be set
        "host": "",  # Should not be set (empty string)
        "port": None,  # Should not be set
        "unix_socket": None,  # Should not be set
        "engine": "django.db.backends.postgresql",
        "vendor": "postgresql",
    }

    _set_db_data(span, mock_cursor)

    # Verify only vendor was set (other values are empty/None)
    expected_calls = [
        mock.call("db.system", "postgresql"),
    ]

    assert span.set_data.call_args_list == expected_calls

    # Verify empty/None values were not set
    not_expected_calls = [
        mock.call("db.name", None),
        mock.call("server.address", ""),
        mock.call("server.port", None),
        mock.call("server.socket.address", None),
    ]

    for call in not_expected_calls:
        assert call not in span.set_data.call_args_list
