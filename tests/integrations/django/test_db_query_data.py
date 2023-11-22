from __future__ import absolute_import

import pytest

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from django.db import connections

from werkzeug.test import Client

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration

from tests.integrations.django.utils import pytest_mark_django_db_decorator
from tests.integrations.django.myapp.wsgi import application

try:
    from unittest.mock import patch
except ImportError:
    from mock import patch


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@patch("sentry_sdk.tracing_utils.DB_SPAN_DURATION_THRESHOLD_MS", 0)
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

    sentry_init(**sentry_options)

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = client.get(reverse("postgres_select_orm"))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT pg_sleep("
        ):
            data = span.get("data")

            assert SPANDATA.CODE_LINENO not in data
            assert SPANDATA.CODE_NAMESPACE not in data
            assert SPANDATA.CODE_FILEPATH not in data
            assert SPANDATA.CODE_FUNCTION not in data


@pytest.mark.forked
@patch("sentry_sdk.tracing_utils.DB_SPAN_DURATION_THRESHOLD_MS", 0)
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
        enable_db_query_source=True,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = client.get(reverse("postgres_select_orm"))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and "auth_user" in span.get("description"):
            data = span.get("data")

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
        assert False, "No db span found"
