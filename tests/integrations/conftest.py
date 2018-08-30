import sys
import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_exception = sentry_sdk.Hub.current.capture_exception

        def capture_exception(error=None):
            given_error = error
            error = error or sys.exc_info()[1]
            if isinstance(error, tuple):
                error = error[1]
            errors.add(error)
            return old_capture_exception(given_error)

        monkeypatch.setattr(
            sentry_sdk.Hub.current, "capture_exception", capture_exception
        )
        return errors

    return inner
