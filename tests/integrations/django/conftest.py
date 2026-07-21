import pytest_django

# pytest-django versions without `databases` support on the django_db marker
# (see `pytest_mark_django_db_decorator` in utils.py) only flush the default
# database between transactional tests, so rows written to the secondary
# postgres database would survive into the next test when the test database
# is reused. Disable --reuse-db there; those envs fall back to recreating
# the database in every forked test.
_reuse_db_supported = False
try:
    pytest_django_version = tuple(map(int, pytest_django.__version__.split(".")))
    _reuse_db_supported = pytest_django_version > (4, 2, 0)
except ValueError:
    _reuse_db_supported = "dev" in pytest_django.__version__
except AttributeError:
    pass


def pytest_configure(config):
    if not _reuse_db_supported and getattr(config.option, "reuse_db", False):
        config.option.reuse_db = False
