from __future__ import absolute_import

import os
import pytest
from datetime import datetime

from sentry_sdk._compat import PY2
from django import VERSION as DJANGO_VERSION
from django.db import connections

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from werkzeug.test import Client

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.tracing_utils import record_sql_queries

from tests.conftest import unpack_werkzeug_response
from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.django.myapp.wsgi import application

try:
    from unittest import mock
except ImportError:
    import mock


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

            if not PY2:
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
