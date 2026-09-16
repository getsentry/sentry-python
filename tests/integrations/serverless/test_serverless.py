import pytest

from sentry_sdk.integrations.serverless import serverless_function


def test_basic(sentry_init, capture_exceptions, monkeypatch):
    sentry_init()
    exceptions = capture_exceptions()

    flush_calls = []

    @serverless_function
    def foo():
        monkeypatch.setattr("sentry_sdk.flush", lambda: flush_calls.append(1))
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

    monkeypatch.setattr("sentry_sdk.flush", lambda: flush_calls.append(1))

    @serverless_function(flush=False)
    def foo():
        1 / 0

    with pytest.raises(ZeroDivisionError):
        foo()

    (exception,) = exceptions
    assert isinstance(exception, ZeroDivisionError)

    assert flush_calls == []

def test_dedupe_reset_between_invocations(sentry_init, capture_events, monkeypatch):
    sentry_init()
    events = capture_events()

    monkeypatch.setattr("sentry_sdk.flush", lambda: None)

    @serverless_function
    def foo():
        1 / 0

    for _ in range(2):
        with pytest.raises(ZeroDivisionError):
            foo()

    assert len(events) == 2, "Each invocation should send an error event and not be deduplicated."
