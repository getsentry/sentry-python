import pytest
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


if _reuse_db_supported:

    @pytest.fixture(scope="session")
    def django_db_setup(
        request,
        django_test_environment,
        django_db_blocker,
        django_db_use_migrations,
        django_db_keepdb,
        django_db_createdb,
        django_db_modify_db_settings,
    ):
        """Override pytest-django's fixture to make forked tests cheap.

        Session fixtures don't survive into @pytest.mark.forked children, so
        every forked test re-runs this fixture in its own process. The stock
        implementation then runs the full setup_databases() machinery against
        postgres on every fork even with --reuse-db. Instead, when the
        reusable test database is already built, just point the connections
        at it; only the in-memory sqlite default database (which never
        outlives a process) is created fresh per fork.
        """
        from django.db import connections
        from django.test.utils import setup_databases, teardown_databases

        if not django_db_use_migrations:
            from pytest_django.fixtures import _disable_migrations

            _disable_migrations()

        keepdb = django_db_keepdb and not django_db_createdb
        db_cfg = None

        with django_db_blocker.unblock():
            reusable = keepdb
            renamed = []
            if reusable:
                for alias in connections:
                    if alias == "default":
                        continue
                    connection = connections[alias]
                    test_name = (
                        connection.settings_dict.get("TEST", {}).get("NAME")
                        or "test_" + connection.settings_dict["NAME"]
                    )
                    original_name = connection.settings_dict["NAME"]
                    connection.settings_dict["NAME"] = test_name
                    renamed.append((connection, original_name))
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT 1 FROM auth_user LIMIT 1")
                    except Exception:
                        reusable = False
                        break
                    finally:
                        connection.close()

            if reusable:
                connections["default"].creation.create_test_db(
                    verbosity=0, autoclobber=True, keepdb=True
                )
            else:
                for connection, original_name in renamed:
                    connection.settings_dict["NAME"] = original_name
                db_cfg = setup_databases(
                    verbosity=request.config.option.verbose,
                    interactive=False,
                    keepdb=keepdb,
                )

        yield

        if db_cfg is not None and not django_db_keepdb:
            with django_db_blocker.unblock():
                try:
                    teardown_databases(db_cfg, verbosity=request.config.option.verbose)
                except Exception as exc:
                    request.node.warn(
                        pytest.PytestWarning(
                            f"Error when trying to teardown test databases: {exc!r}"
                        )
                    )
