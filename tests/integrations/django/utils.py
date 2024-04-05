from functools import partial

import pytest

# Request access to all configured databases 
# (we have "default" and "postgres")
pytest_mark_django_db = partial(pytest.mark.django_db, databases="__all__")
