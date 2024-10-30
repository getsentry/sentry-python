import asyncio
import concurrent.futures as cf

import ldclient

import sentry_sdk

from ldclient.config import Config
from ldclient.context import Context
from ldclient.integrations.test_data import TestData

from sentry_sdk.integrations.launchdarkly import LaunchDarklyIntegration


def test_launchdarkly_integration(sentry_init):
    td = TestData.data_source()
    config = Config("sdk-key", update_processor_class=td)
    ldclient.set_config(config)
    client = ldclient.get()
    sentry_init(integrations=[LaunchDarklyIntegration()])

    # Set test values
    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(True))

    # Evaluate
    client.variation("hello", Context.create("my-org", "organization"), False)
    client.variation("world", Context.create("user1", "user"), False)
    client.variation("other", Context.create("user2", "user"), False)

    assert sentry_sdk.get_current_scope().flags.get() == [
        {"flag": "hello", "result": True},
        {"flag": "world", "result": True},
        {"flag": "other", "result": False},
    ]

    ldclient._reset_client()


def test_launchdarkly_integration_threaded(sentry_init):
    td = TestData.data_source()
    client = ldclient.get()
    sentry_init(integrations=[LaunchDarklyIntegration()])
    context = Context.create("user1")

    def task(flag_key):
        # Creates a new isolation scope for the thread.
        # This means the evaluations in each task are captured separately.
        with sentry_sdk.isolation_scope():
            client.variation(flag_key, context, False)
            return [f["flag"] for f in sentry_sdk.get_current_scope().flags.get()]

    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(False))
    # Capture an eval before we split isolation scopes.
    client.variation("hello", context, False)

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, ["world", "other"]))

    assert results[0] == ["hello", "world"]
    assert results[1] == ["hello", "other"]

    ldclient._reset_client()


def test_launchdarkly_integration_asyncio(sentry_init):
    """Assert concurrently evaluated flags do not pollute one another."""
    td = TestData.data_source()
    client = ldclient.get()
    sentry_init(integrations=[LaunchDarklyIntegration()])
    context = Context.create("user1")

    async def task(flag_key):
        with sentry_sdk.isolation_scope():
            client.variation(flag_key, context, False)
            return [f["flag"] for f in sentry_sdk.get_current_scope().flags.get()]

    async def runner():
        return asyncio.gather(task("world"), task("other"))

    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(False))
    client.variation("hello", context, False)

    results = asyncio.run(runner()).result()
    assert results[0] == ["hello", "world"]
    assert results[1] == ["hello", "other"]

    ldclient._reset_client()
