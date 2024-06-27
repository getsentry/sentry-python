import random
from unittest import mock

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
    sentry_init, capture_client_reports, monkeypatch
):
    sentry_init(
        traces_sample_rate=1.0,
        transport=UnhealthyTestTransport(),
    )

    reports = capture_client_reports()

    monitor = sentry_sdk.get_client().monitor
    monitor.interval = 0.1

    # make sure rng doesn't sample
    monkeypatch.setattr(random, "random", lambda: 0.9)

    assert monitor.is_healthy() is True
    monitor.run()
    assert monitor.is_healthy() is False
    assert monitor.downsample_factor == 1

    with sentry_sdk.start_transaction(name="foobar") as transaction:
        assert transaction.sampled is False
        assert transaction.sample_rate == 0.5

    assert reports == [("backpressure", "transaction")]


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
