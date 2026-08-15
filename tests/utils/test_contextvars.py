import builtins
import random
import sys
import time
import types
from unittest import mock

import pytest


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


@pytest.mark.forked
@mock.patch("sentry_sdk.utils._is_contextvars_broken", return_value=True)
def test_leaks_when_is_contextvars_broken_is_false(maybe_monkeypatched_threading):
    _run_contextvar_threaded_test()


def test_is_contextvars_broken_survives_eventlet_attributeerror(monkeypatch):
    """
    Regression test for https://github.com/getsentry/sentry-python/issues/7202

    Importing eventlet/greenlet can raise errors other than ImportError
    depending on which combination of eventlet, greenlet, and other
    monkeypatched modules (e.g. dnspython, httpcore) happen to be installed.
    _is_contextvars_broken() should not crash in that case, and it should not
    leave a broken partially-imported module behind in sys.modules.
    """
    from sentry_sdk import utils

    monkeypatch.delitem(sys.modules, "greenlet", raising=False)
    monkeypatch.delitem(sys.modules, "eventlet", raising=False)
    monkeypatch.delitem(sys.modules, "eventlet.patcher", raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "greenlet":
            sys.modules["greenlet"] = types.ModuleType("greenlet")
            raise AttributeError("module 'dns.rdtypes' has no attribute 'ANY'")
        return real_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=fake_import):
        with mock.patch(
            "gevent.monkey.is_object_patched", return_value=False, create=True
        ):
            with mock.patch.dict(sys.modules, {"gevent": None}, clear=False):
                result = utils._is_contextvars_broken()

    assert result is False
    assert "greenlet" not in sys.modules
