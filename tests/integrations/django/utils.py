from functools import partial

import pytest

# Request access to all configured databases
# (we have "default" and "postgres" at the time of writing this)
try:
    pytest_mark_django_db = partial(pytest.mark.django_db, databases="__all__")
except (TypeError, ValueError, AttributeError):
    pytest_mark_django_db = partial(pytest.mark.django_db)
