from functools import partial

import pytest
import pytest_django


# Hack to prevent from experimental feature introduced in version `4.3.0` in `pytest-django` that
# requires explicit database allow from failing the test
pytest_mark_django_db_decorator = partial(pytest.mark.django_db)
try:
    pytest_version = tuple(map(int, pytest_django.__version__.split(".")))
    if pytest_version > (4, 2, 0):
        pytest_mark_django_db_decorator = partial(
            pytest.mark.django_db, databases="__all__"
        )
except ValueError:
    if "dev" in pytest_django.__version__:
        pytest_mark_django_db_decorator = partial(
            pytest.mark.django_db, databases="__all__"
        )
except AttributeError:
    pass
