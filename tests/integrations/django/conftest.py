import pytest
import threading

from django.db import connections


@pytest.fixture(autouse=True)
def clear_caches():
    """Invalidate the connection caches.

    https://github.com/pytest-dev/pytest-django/issues/587
    """
    connections._connections = threading.local()
    # this will clear the cached property
    connections.__dict__.pop("databases", None)
