from __future__ import absolute_import

import pytest

from django import VERSION as DJANGO_VERSION
from django.db import connections

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from werkzeug.test import Client

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration

from tests.conftest import unpack_werkzeug_response
from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.django.myapp.wsgi import application


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
@pytest.mark.parametrize("enable_db_query_source", [None, False])
def test_query_source_disabled(
    sentry_init, client, capture_events, enable_db_query_source
):
    sentry_options = {
        "integrations": [DjangoIntegration()],
        "send_default_pii": True,
        "traces_sample_rate": 1.0,
    }
    if enable_db_query_source is not None:
        sentry_options["enable_db_query_source"] = enable_db_query_source
        sentry_options["db_query_source_threshold_ms"] = 0

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
