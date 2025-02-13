import concurrent.futures as cf
import sys
from contextlib import contextmanager
from statsig import statsig
from statsig.statsig_user import StatsigUser
from random import random
from unittest.mock import Mock

import pytest

import sentry_sdk
from sentry_sdk.integrations.statsig import StatsigIntegration


@contextmanager
def mock_statsig(gate_dict):
    old_check_gate = statsig.check_gate

    def mock_check_gate(user, gate, *args, **kwargs):
        return gate_dict.get(gate, False)

    statsig.check_gate = Mock(side_effect=mock_check_gate)

    yield

    statsig.check_gate = old_check_gate


def test_check_gate(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(StatsigIntegration.identifier)

    with mock_statsig({"hello": True, "world": False}):
        sentry_init(integrations=[StatsigIntegration()])
        events = capture_events()
        user = StatsigUser(user_id="user-id")

        statsig.check_gate(user, "hello")
        statsig.check_gate(user, "world")
        statsig.check_gate(user, "other")  # unknown gates default to False.

        sentry_sdk.capture_exception(Exception("something wrong!"))

        assert len(events) == 1
        assert events[0]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
                {"flag": "world", "result": False},
                {"flag": "other", "result": False},
            ]
        }


def test_check_gate_threaded(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(StatsigIntegration.identifier)

    with mock_statsig({"hello": True, "world": False}):
        sentry_init(integrations=[StatsigIntegration()])
        events = capture_events()
        user = StatsigUser(user_id="user-id")

        # Capture an eval before we split isolation scopes.
        statsig.check_gate(user, "hello")

        def task(flag_key):
            # Creates a new isolation scope for the thread.
            # This means the evaluations in each task are captured separately.
            with sentry_sdk.isolation_scope():
                statsig.check_gate(user, flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            pool.map(task, ["world", "other"])

        # Capture error in original scope
        sentry_sdk.set_tag("task_id", "0")
        sentry_sdk.capture_exception(Exception("something wrong!"))

        assert len(events) == 3
        events.sort(key=lambda e: e["tags"]["task_id"])

        assert events[0]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
            ]
        }
        assert events[1]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
                {"flag": "other", "result": False},
            ]
        }
        assert events[2]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
                {"flag": "world", "result": False},
            ]
        }


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_check_gate_asyncio(sentry_init, capture_events, uninstall_integration):
    asyncio = pytest.importorskip("asyncio")
    uninstall_integration(StatsigIntegration.identifier)

    with mock_statsig({"hello": True, "world": False}):
        sentry_init(integrations=[StatsigIntegration()])
        events = capture_events()
        user = StatsigUser(user_id="user-id")

        # Capture an eval before we split isolation scopes.
        statsig.check_gate(user, "hello")

        async def task(flag_key):
            with sentry_sdk.isolation_scope():
                statsig.check_gate(user, flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        async def runner():
            return asyncio.gather(task("world"), task("other"))

        asyncio.run(runner())

        # Capture error in original scope
        sentry_sdk.set_tag("task_id", "0")
        sentry_sdk.capture_exception(Exception("something wrong!"))

        assert len(events) == 3
        events.sort(key=lambda e: e["tags"]["task_id"])

        assert events[0]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
            ]
        }
        assert events[1]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
                {"flag": "other", "result": False},
            ]
        }
        assert events[2]["contexts"]["flags"] == {
            "values": [
                {"flag": "hello", "result": True},
                {"flag": "world", "result": False},
            ]
        }


def test_wraps_original(sentry_init, uninstall_integration):
    uninstall_integration(StatsigIntegration.identifier)
    flag_value = random() < 0.5

    with mock_statsig(
        {"test-flag": flag_value}
    ):  # patches check_gate with a Mock object.
        mock_check_gate = statsig.check_gate
        sentry_init(integrations=[StatsigIntegration()])  # wraps check_gate.
        user = StatsigUser(user_id="user-id")

        res = statsig.check_gate(user, "test-flag", "extra-arg", kwarg=1)  # type: ignore[arg-type]

        assert res == flag_value
        assert mock_check_gate.call_args == (  # type: ignore[attr-defined]
            (user, "test-flag", "extra-arg"),
            {"kwarg": 1},
        )


def test_wrapper_attributes(sentry_init, uninstall_integration):
    uninstall_integration(StatsigIntegration.identifier)
    original_check_gate = statsig.check_gate
    sentry_init(integrations=[StatsigIntegration()])

    # Methods have not lost their qualified names after decoration.
    assert statsig.check_gate.__name__ == "check_gate"
    assert statsig.check_gate.__qualname__ == original_check_gate.__qualname__

    # Clean up
    statsig.check_gate = original_check_gate
