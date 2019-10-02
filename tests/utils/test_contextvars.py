import random
import time


from sentry_sdk.utils import _is_threading_local_monkey_patched


def test_thread_local_is_patched(maybe_monkeypatched_threading):
    if maybe_monkeypatched_threading is None:
        assert not _is_threading_local_monkey_patched()
    else:
        assert _is_threading_local_monkey_patched()


def test_leaks(maybe_monkeypatched_threading):
    import threading

    # Need to explicitly call _get_contextvars because the SDK has already
    # decided upon gevent on import.

    from sentry_sdk import utils

    _, ContextVar = utils._get_contextvars()

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
