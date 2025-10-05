import sentry_sdk
from sentry_sdk._n_plus_one import is_ignoring_n_plus_one


def test_context_manager_api():
    assert not is_ignoring_n_plus_one()
    with sentry_sdk.ignore_n_plus_one():
        assert is_ignoring_n_plus_one()
    assert not is_ignoring_n_plus_one()


def test_decorator_api():
    assert not is_ignoring_n_plus_one()

    @sentry_sdk.ignore_n_plus_one
    def fn():
        return is_ignoring_n_plus_one()

    assert fn() is True
    assert not is_ignoring_n_plus_one()
