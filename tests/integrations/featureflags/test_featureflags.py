import asyncio
import concurrent.futures as cf

import pytest

import sentry_sdk
from sentry_sdk.integrations import _processed_integrations, _installed_integrations
from sentry_sdk.integrations.featureflags import FeatureFlagsIntegration


@pytest.fixture
def uninstall_integration():
    """Forces the next call to sentry_init to re-install/setup an integration."""

    def inner(identifier):
        _processed_integrations.discard(identifier)
        _installed_integrations.discard(identifier)

    return inner


def test_featureflags_integration(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(FeatureFlagsIntegration.identifier)
    sentry_init(integrations=[FeatureFlagsIntegration()])
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)

    flags_integration.set_flag("hello", False)
    flags_integration.set_flag("world", True)
    flags_integration.set_flag("other", False)

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))
    [event] = events

    assert event["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "world", "result": True},
            {"flag": "other", "result": False},
        ]
    }


def test_featureflags_integration_threaded(
    sentry_init, capture_events, uninstall_integration
):
    uninstall_integration(FeatureFlagsIntegration.identifier)
    sentry_init(integrations=[FeatureFlagsIntegration()])
    events = capture_events()

    # Capture an eval before we split isolation scopes.
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)
    flags_integration.set_flag("hello", False)

    def task(flag_key):
        # Creates a new isolation scope for the thread.
        # This means the evaluations in each task are captured separately.
        with sentry_sdk.isolation_scope():
            flags_integration = sentry_sdk.get_client().get_integration(
                FeatureFlagsIntegration
            )
            flags_integration.set_flag(flag_key, False)
            # use a tag to identify to identify events later on
            sentry_sdk.set_tag("task_id", flag_key)
            sentry_sdk.capture_exception(Exception("something wrong!"))

    # Run tasks in separate threads
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        pool.map(task, ["world", "other"])

    # Capture error in original scope
    sentry_sdk.set_tag("task_id", "0")
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 3
    events.sort(key=lambda e: e["tags"]["task_id"])

    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
        ]
    }
    assert events[1]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }
    assert events[2]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "world", "result": False},
        ]
    }


def test_featureflags_integration_asyncio(
    sentry_init, capture_events, uninstall_integration
):
    uninstall_integration(FeatureFlagsIntegration.identifier)
    sentry_init(integrations=[FeatureFlagsIntegration()])
    events = capture_events()

    # Capture an eval before we split isolation scopes.
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)
    flags_integration.set_flag("hello", False)

    async def task(flag_key):
        # Creates a new isolation scope for the thread.
        # This means the evaluations in each task are captured separately.
        with sentry_sdk.isolation_scope():
            flags_integration = sentry_sdk.get_client().get_integration(
                FeatureFlagsIntegration
            )
            flags_integration.set_flag(flag_key, False)
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
            {"flag": "hello", "result": False},
        ]
    }
    assert events[1]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }
    assert events[2]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "world", "result": False},
        ]
    }
