import pytest

from sentry_sdk.integrations.serverless import serverless_function


def test_basic(sentry_init, capture_exceptions, monkeypatch):
    sentry_init()
    exceptions = capture_exceptions()

    flush_calls = []

    @serverless_function
    def foo():
        monkeypatch.setattr(
            "sentry_sdk.Hub.current.flush", lambda: flush_calls.append(1)
        )
        1 / 0

    with pytest.raises(ZeroDivisionError):
        foo()

    (exception,) = exceptions
    assert isinstance(exception, ZeroDivisionError)

    assert flush_calls == [1]


def test_flush_disabled(sentry_init, capture_exceptions, monkeypatch):
    sentry_init()
    exceptions = capture_exceptions()

    flush_calls = []

    monkeypatch.setattr("sentry_sdk.Hub.current.flush", lambda: flush_calls.append(1))

    @serverless_function(flush=False)
    def foo():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        foo()

    (exception,) = exceptions
    assert isinstance(exception, ZeroDivisionError)

    assert flush_calls == []
