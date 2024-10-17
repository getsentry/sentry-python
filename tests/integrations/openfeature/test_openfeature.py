import asyncio
import concurrent.futures as cf
import sentry_sdk

from openfeature import api
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from sentry_sdk.integrations.openfeature import OpenFeatureIntegration


def test_openfeature_integration(sentry_init):
    sentry_init(integrations=[OpenFeatureIntegration()])

    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))

    client = api.get_client()
    client.get_boolean_value("hello", default_value=False)
    client.get_boolean_value("world", default_value=False)
    client.get_boolean_value("other", default_value=True)

    assert sentry_sdk.get_current_scope().flags.get() == [
        {"flag": "hello", "result": True},
        {"flag": "world", "result": False},
        {"flag": "other", "result": True},
    ]


def test_openfeature_integration_threaded(sentry_init):
    sentry_init(integrations=[OpenFeatureIntegration()])

    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))

    client = api.get_client()
    client.get_boolean_value("hello", default_value=False)

    def task(flag):
        # Create a new isolation scope for the thread. This means the flags
        with sentry_sdk.isolation_scope():
            client.get_boolean_value(flag, default_value=False)
            return [f["flag"] for f in sentry_sdk.get_current_scope().flags.get()]

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, ["world", "other"]))

    assert results[0] == ["hello", "world"]
    assert results[1] == ["hello", "other"]


def test_openfeature_integration_asyncio(sentry_init):
    """Assert concurrently evaluated flags do not pollute one another."""

    async def task(flag):
        with sentry_sdk.isolation_scope():
            client.get_boolean_value(flag, default_value=False)
            return [f["flag"] for f in sentry_sdk.get_current_scope().flags.get()]

    async def runner():
        return asyncio.gather(task("world"), task("other"))

    sentry_init(integrations=[OpenFeatureIntegration()])

    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))

    client = api.get_client()
    client.get_boolean_value("hello", default_value=False)

    results = asyncio.run(runner()).result()
    assert results[0] == ["hello", "world"]
    assert results[1] == ["hello", "other"]
