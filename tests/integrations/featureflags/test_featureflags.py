import asyncio
import concurrent.futures as cf

import sentry_sdk
from sentry_sdk.integrations import _processed_integrations
from sentry_sdk.integrations.featureflags import FeatureFlagsIntegration


def test_featureflags_integration(sentry_init, capture_events):
    _processed_integrations.discard(
        FeatureFlagsIntegration.identifier
    )  # force reinstall
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


def test_featureflags_integration_threaded(sentry_init, capture_events):
    _processed_integrations.discard(
        FeatureFlagsIntegration.identifier
    )  # force reinstall
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
            sentry_sdk.set_tag("flag_key", flag_key)
            sentry_sdk.capture_exception(Exception("something wrong!"))

    # Run tasks in separate threads
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        pool.map(task, ["world", "other"])

    assert len(events) == 2
    events.sort(key=lambda e: e["tags"]["flag_key"])
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }
    assert events[1]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "world", "result": False},
        ]
    }


def test_featureflags_integration_asyncio(sentry_init, capture_events):
    _processed_integrations.discard(
        FeatureFlagsIntegration.identifier
    )  # force reinstall
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
            sentry_sdk.set_tag("flag_key", flag_key)
            sentry_sdk.capture_exception(Exception("something wrong!"))

    async def runner():
        return asyncio.gather(task("world"), task("other"))

    asyncio.run(runner())

    assert len(events) == 2
    events.sort(key=lambda e: e["tags"]["flag_key"])
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }
    assert events[1]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "world", "result": False},
        ]
    }
