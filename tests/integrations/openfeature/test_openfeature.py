import concurrent.futures as cf
import sys
import pytest

from openfeature import api
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider

import sentry_sdk
from sentry_sdk.integrations.openfeature import OpenFeatureIntegration


@pytest.fixture
def reset_openfeature(uninstall_integration):
    yield

    # Teardown
    uninstall_integration(OpenFeatureIntegration.identifier)
    api.clear_hooks()
    api.shutdown()  # provider clean up


@pytest.mark.parametrize(
    "use_global_client",
    (False, True),
)
def test_openfeature_integration(
    sentry_init, use_global_client, capture_events, reset_openfeature
):
    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))
    client = api.get_client()

    if use_global_client:
        sentry_init(integrations=[OpenFeatureIntegration()])
    else:
        sentry_init(integrations=[OpenFeatureIntegration(client=client)])

    client.get_boolean_value("hello", default_value=False)
    client.get_boolean_value("world", default_value=False)
    client.get_boolean_value("other", default_value=True)

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": True},
            {"flag": "world", "result": False},
            {"flag": "other", "result": True},
        ]
    }


def test_openfeature_integration_threaded(
    sentry_init, capture_events, reset_openfeature
):
    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))
    client = api.get_client()

    sentry_init(integrations=[OpenFeatureIntegration(client=client)])
    events = capture_events()

    # Capture an eval before we split isolation scopes.
    client.get_boolean_value("hello", default_value=False)

    def task(flag):
        # Create a new isolation scope for the thread. This means the flags
        with sentry_sdk.isolation_scope():
            client.get_boolean_value(flag, default_value=False)
            # use a tag to identify to identify events later on
            sentry_sdk.set_tag("task_id", flag)
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
def test_openfeature_integration_asyncio(
    sentry_init, capture_events, reset_openfeature
):
    """Assert concurrently evaluated flags do not pollute one another."""

    asyncio = pytest.importorskip("asyncio")

    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))
    client = api.get_client()

    sentry_init(integrations=[OpenFeatureIntegration(client=client)])
    events = capture_events()

    # Capture an eval before we split isolation scopes.
    client.get_boolean_value("hello", default_value=False)

    async def task(flag):
        with sentry_sdk.isolation_scope():
            client.get_boolean_value(flag, default_value=False)
            # use a tag to identify to identify events later on
            sentry_sdk.set_tag("task_id", flag)
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


def test_openfeature_integration_client_isolation(
    sentry_init, capture_events, reset_openfeature
):
    """
    If the integration is tracking a single client, evaluations from other clients should not be
    captured.
    """
    flags = {
        "hello": InMemoryFlag("on", {"on": True, "off": False}),
        "world": InMemoryFlag("off", {"on": True, "off": False}),
    }
    api.set_provider(InMemoryProvider(flags))
    client = api.get_client()
    sentry_init(integrations=[OpenFeatureIntegration(client=client)])

    other_client = api.get_client()
    other_client.get_boolean_value("hello", default_value=False)
    other_client.get_boolean_value("world", default_value=False)
    other_client.get_boolean_value("other", default_value=True)

    events = capture_events()
    sentry_sdk.set_tag("apple", "0")
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {"values": []}
