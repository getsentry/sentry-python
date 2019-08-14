import random
import time

import pytest
import gevent


from sentry_sdk.utils import _is_threading_local_monkey_patched


def try_gevent_patch_all():
    try:
        gevent.monkey.patch_all()
    except Exception as e:
        if "_RLock__owner" in str(e):
            pytest.skip("https://github.com/gevent/gevent/issues/1380")
        else:
            raise


def test_gevent_is_patched():
    try_gevent_patch_all()
    assert _is_threading_local_monkey_patched()


def test_gevent_is_not_patched():
    assert not _is_threading_local_monkey_patched()


@pytest.mark.parametrize("with_gevent", [True, False])
def test_leaks(with_gevent):
    if with_gevent:
        try_gevent_patch_all()

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
