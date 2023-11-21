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


@pytest.fixture
def client():
    return Client(application)


@pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    if "postgres" not in connections:
        pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    connections["postgres"].connection = None

    events = capture_events()

    _, status, _ = client.get(reverse("postgres_select_slow"))
    assert status == "200 OK"

    (event,) = events
    for span in event["spans"]:
        if span.get("op") == "db" and span.get("description").startswith(
            "SELECT pg_sleep("
        ):
            data = span.get("data")

            assert SPANDATA.CODE_LINENO in data
            assert SPANDATA.CODE_NAMESPACE in data
            assert SPANDATA.CODE_FILEPATH in data
            assert SPANDATA.CODE_FUNCTION in data

            assert data.get(SPANDATA.CODE_LINENO) == 201
            assert (
                data.get(SPANDATA.CODE_NAMESPACE)
                == "tests.integrations.django.myapp.views"
            )
            assert data.get(SPANDATA.CODE_FILEPATH).endswith(
                "tests/integrations/django/myapp/views.py"
            )
            assert data.get(SPANDATA.CODE_FUNCTION) == "postgres_select_slow"
