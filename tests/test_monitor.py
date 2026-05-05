import os
import sys
from collections import Counter
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.transport import Transport


class HealthyTestTransport(Transport):
    def capture_envelope(self, _):
        pass

    def is_healthy(self):
        return True


class UnhealthyTestTransport(HealthyTestTransport):
    def is_healthy(self):
        return False


def test_no_monitor_if_disabled(sentry_init):
    sentry_init(
        transport=HealthyTestTransport(),
        enable_backpressure_handling=False,
    )

    assert sentry_sdk.get_client().monitor is None


def test_monitor_if_enabled(sentry_init):
    sentry_init(transport=HealthyTestTransport())

    monitor = sentry_sdk.get_client().monitor
    assert monitor is not None
    assert monitor._thread is None

    assert monitor.is_healthy() is True
    assert monitor.downsample_factor == 0
    assert monitor._thread is not None
    assert monitor._thread.name == "sentry.monitor"


def test_monitor_unhealthy(sentry_init):
    sentry_init(transport=UnhealthyTestTransport())

    monitor = sentry_sdk.get_client().monitor
    monitor.interval = 0.1

    assert monitor.is_healthy() is True

    for i in range(15):
        monitor.run()
        assert monitor.is_healthy() is False
        assert monitor.downsample_factor == (i + 1 if i < 10 else 10)


def test_transaction_uses_downsampled_rate(
    sentry_init, capture_record_lost_event_calls, monkeypatch
):
    sentry_init(
        traces_sample_rate=1.0,
        transport=UnhealthyTestTransport(),
    )

    record_lost_event_calls = capture_record_lost_event_calls()

    monitor = sentry_sdk.get_client().monitor
    monitor.interval = 0.1

    assert monitor.is_healthy() is True
    monitor.run()
    assert monitor.is_healthy() is False
    assert monitor.downsample_factor == 1

    # make sure we don't sample the transaction
    with mock.patch("sentry_sdk.tracing_utils.Random.randrange", return_value=750000):
        with sentry_sdk.start_transaction(name="foobar") as transaction:
            assert transaction.sampled is False
            assert transaction.sample_rate == 0.5

    assert Counter(record_lost_event_calls) == Counter(
        [
            ("backpressure", "transaction", None, 1),
            ("backpressure", "span", None, 1),
        ]
    )


def test_monitor_no_thread_on_shutdown_no_errors(sentry_init):
    sentry_init(transport=HealthyTestTransport())

    # make it seem like the interpreter is shutting down
    with mock.patch(
        "threading.Thread.start",
        side_effect=RuntimeError("can't create new thread at interpreter shutdown"),
    ):
        monitor = sentry_sdk.get_client().monitor
        assert monitor is not None
        assert monitor._thread is None
        monitor.run()
        assert monitor._thread is None


@pytest.mark.skipif(
    sys.platform == "win32"
    or not hasattr(os, "fork")
    or not hasattr(os, "register_at_fork"),
    reason="requires POSIX fork and os.register_at_fork (Python 3.7+)",
)
def test_monitor_thread_lock_reset_in_child_after_fork(sentry_init):
    """Regression test for #6148.

    If os.fork() runs while Monitor._thread_lock is held, the child
    inherits the lock locked. The holding thread does not exist in the
    child, so the lock can never be released and _ensure_running
    deadlocks forever. The after-fork hook must replace the lock with
    a fresh one in the child.
    """
    sentry_init(transport=HealthyTestTransport())
    monitor = sentry_sdk.get_client().monitor
    assert monitor is not None

    original_lock = monitor._thread_lock
    original_lock.acquire()
    pid = os.fork()
    if pid == 0:
        # Child: was the lock object replaced and is the new one not
        # held? Without the fix, _thread_lock is `original_lock`
        # inherited locked, so `replaced` is False. blocking=False
        # guarantees the child can't hang on a regression.
        replaced = monitor._thread_lock is not original_lock
        unheld = monitor._thread_lock.acquire(blocking=False)
        os._exit(0 if replaced and unheld else 1)

    original_lock.release()
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
