from __future__ import absolute_import

import os

# import pytest

try:
    from django.urls import reverse
except ImportError:
    from django.core.urlresolvers import reverse

from django.db import connections

from tests.integrations.django.utils import pytest_mark_django_db_decorator

from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.django import DjangoIntegration


# @pytest.mark.forked
@pytest_mark_django_db_decorator(transaction=True)
def test_query_source(sentry_init, client, capture_events):
    sentry_init(
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        traces_sample_rate=1.0,
    )

    # if "postgres" not in connections:
    #     pytest.skip("postgres tests disabled")

    # trigger Django to open a new connection by marking the existing one as None.
    # connections["postgres"].connection = None

    events = capture_events()

    url = reverse("postgres_select")
    # import ipdb

    # ipdb.set_trace()
    content, status, headers = client.get(url)
    assert status == "200 OK"

    (event,) = events

    for span in event["spans"]:
        if span.get("op") == "db":
            data = span.get("data")
            assert data.get(SPANDATA.DB_SYSTEM) == "postgresql"
            conn_params = connections["postgres"].get_connection_params()
            assert data.get(SPANDATA.DB_NAME) is not None
            assert data.get(SPANDATA.DB_NAME) == conn_params.get(
                "database"
            ) or conn_params.get("dbname")
            assert data.get(SPANDATA.SERVER_ADDRESS) == os.environ.get(
                "SENTRY_PYTHON_TEST_POSTGRES_HOST", "localhost"
            )
            assert data.get(SPANDATA.SERVER_PORT) == "5432"
