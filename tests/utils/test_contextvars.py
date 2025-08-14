import pytest
import random
import time


def _run_contextvar_threaded_test():
    import threading

    # Need to explicitly call _get_contextvars because the SDK has already
    # decided upon gevent on import.

    from sentry_sdk import utils

    _, ContextVar = utils._get_contextvars()  # noqa: N806

    ts = []

    var = ContextVar("test_contextvar_leaks")

    success = []

    def run():
        value = int(random.random() * 1000)
        var.set(value)

        for _ in range(100):
            time.sleep(0)
            assert var.get(None) == value

        success.append(1)

    for _ in range(20):
        t = threading.Thread(target=run)
        t.start()
        ts.append(t)

    for t in ts:
        t.join()

    assert len(success) == 20


@pytest.mark.forked
def test_leaks(maybe_monkeypatched_threading):
    _run_contextvar_threaded_test()
