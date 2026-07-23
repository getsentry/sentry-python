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


# Shared parametrization test matrix exercising the precedence between the legacy
# ``send_default_pii`` boolean and the ``data_collection.user_info`` setting.
# The second value indicates whether user info is expected to be collected.
USER_INFO_INIT_KWARGS = [
    pytest.param({"send_default_pii": True}, True, id="legacy_send_default_pii_true"),
    pytest.param(
        {"send_default_pii": False}, False, id="legacy_send_default_pii_false"
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": True}}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": False}}},
        False,
        id="data_collection_user_info_false",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"user_info": False}},
        },
        False,
        id="data_collection_wins_over_send_default_pii_true",
    ),
    pytest.param(
        {
            "send_default_pii": False,
            "_experiments": {"data_collection": {"user_info": True}},
        },
        True,
        id="data_collection_wins_over_send_default_pii_false",
    ),
]
