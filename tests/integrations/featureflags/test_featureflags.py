import asyncio
import concurrent.futures as cf

import sentry_sdk
from sentry_sdk.integrations.featureflags import FeatureFlagsIntegration


def test_featureflags_integration(sentry_init):
    sentry_init(integrations=[FeatureFlagsIntegration()])
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)

    flags_integration.set_flag("hello", False)
    flags_integration.set_flag("world", True)
    flags_integration.set_flag("other", False)

    assert sentry_sdk.get_current_scope().flags.get() == [
        {"flag": "hello", "result": False},
        {"flag": "world", "result": True},
        {"flag": "other", "result": False},
    ]


def test_featureflags_integration_threaded(sentry_init):
    sentry_init(integrations=[FeatureFlagsIntegration()])
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)

    def task(flag_key):
        # Creates a new isolation scope for the thread.
        # This means the evaluations in each task are captured separately.
        with sentry_sdk.isolation_scope():
            flags_integration.set_flag(flag_key, False)
            return sentry_sdk.get_current_scope().flags.get()

    # Capture an eval before we split isolation scopes.
    flags_integration.set_flag("hello", False)

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, ["world", "other"]))

    assert results[0] == [
        {"flag": "hello", "result": False},
        {"flag": "world", "result": False},
    ]
    assert results[1] == [
        {"flag": "hello", "result": False},
        {"flag": "other", "result": False},
    ]


def test_featureflags_integration_asyncio(sentry_init):
    """Assert concurrently evaluated flags do not pollute one another."""
    sentry_init(integrations=[FeatureFlagsIntegration()])
    flags_integration = sentry_sdk.get_client().get_integration(FeatureFlagsIntegration)

    async def task(flag_key):
        with sentry_sdk.isolation_scope():
            flags_integration.set_flag(flag_key, False)
            return sentry_sdk.get_current_scope().flags.get()

    async def runner():
        return asyncio.gather(task("world"), task("other"))

    flags_integration.set_flag("hello", False)

    results = asyncio.run(runner()).result()
    assert results[0] == [
        {"flag": "hello", "result": False},
        {"flag": "world", "result": False},
    ]
    assert results[1] == [
        {"flag": "hello", "result": False},
        {"flag": "other", "result": False},
    ]
