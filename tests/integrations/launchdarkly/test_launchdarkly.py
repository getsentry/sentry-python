import concurrent.futures as cf
import sys

import ldclient
import pytest

from ldclient import LDClient
from ldclient.config import Config
from ldclient.context import Context
from ldclient.integrations.test_data import TestData

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.launchdarkly import LaunchDarklyIntegration
from sentry_sdk import start_span, start_transaction
from tests.conftest import ApproxDict


@pytest.mark.parametrize(
    "use_global_client",
    (False, True),
)
def test_launchdarkly_integration(
    sentry_init, use_global_client, capture_events, uninstall_integration
):
    td = TestData.data_source()
    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(True))
    # Disable background requests as we aren't using a server.
    config = Config(
        "sdk-key", update_processor_class=td, diagnostic_opt_out=True, send_events=False
    )

    uninstall_integration(LaunchDarklyIntegration.identifier)
    if use_global_client:
        ldclient.set_config(config)
        sentry_init(integrations=[LaunchDarklyIntegration()])
        client = ldclient.get()
    else:
        client = LDClient(config=config)
        sentry_init(integrations=[LaunchDarklyIntegration(ld_client=client)])

    # Evaluate
    client.variation("hello", Context.create("my-org", "organization"), False)
    client.variation("world", Context.create("user1", "user"), False)
    client.variation("other", Context.create("user2", "user"), False)

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": True},
            {"flag": "world", "result": True},
            {"flag": "other", "result": False},
        ]
    }


def test_launchdarkly_integration_threaded(
    sentry_init, capture_events, uninstall_integration
):
    td = TestData.data_source()
    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(True))
    client = LDClient(
        config=Config(
            "sdk-key",
            update_processor_class=td,
            diagnostic_opt_out=True,  # Disable background requests as we aren't using a server.
            send_events=False,
        )
    )
    context = Context.create("user1")

    uninstall_integration(LaunchDarklyIntegration.identifier)
    sentry_init(integrations=[LaunchDarklyIntegration(ld_client=client)])
    events = capture_events()

    def task(flag_key):
        # Creates a new isolation scope for the thread.
        # This means the evaluations in each task are captured separately.
        with sentry_sdk.isolation_scope():
            client.variation(flag_key, context, False)
            # use a tag to identify to identify events later on
            sentry_sdk.set_tag("task_id", flag_key)
            sentry_sdk.capture_exception(Exception("something wrong!"))

    # Capture an eval before we split isolation scopes.
    client.variation("hello", context, False)

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
            {"flag": "world", "result": True},
        ]
    }


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_launchdarkly_integration_asyncio(
    sentry_init, capture_events, uninstall_integration
):
    """Assert concurrently evaluated flags do not pollute one another."""

    asyncio = pytest.importorskip("asyncio")

    td = TestData.data_source()
    td.update(td.flag("hello").variation_for_all(True))
    td.update(td.flag("world").variation_for_all(True))
    client = LDClient(
        config=Config(
            "sdk-key",
            update_processor_class=td,
            diagnostic_opt_out=True,  # Disable background requests as we aren't using a server.
            send_events=False,
        )
    )
    context = Context.create("user1")

    uninstall_integration(LaunchDarklyIntegration.identifier)
    sentry_init(integrations=[LaunchDarklyIntegration(ld_client=client)])
    events = capture_events()

    async def task(flag_key):
        with sentry_sdk.isolation_scope():
            client.variation(flag_key, context, False)
            # use a tag to identify to identify events later on
            sentry_sdk.set_tag("task_id", flag_key)
            sentry_sdk.capture_exception(Exception("something wrong!"))

    async def runner():
        return asyncio.gather(task("world"), task("other"))

    # Capture an eval before we split isolation scopes.
    client.variation("hello", context, False)

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
            {"flag": "world", "result": True},
        ]
    }


def test_launchdarkly_integration_did_not_enable(monkeypatch):
    # Client is not passed in and set_config wasn't called.
    # TODO: Bad practice to access internals like this. We can skip this test, or remove this
    #  case entirely (force user to pass in a client instance).
    ldclient._reset_client()
    try:
        ldclient.__lock.lock()
        ldclient.__config = None
    finally:
        ldclient.__lock.unlock()

    with pytest.raises(DidNotEnable):
        LaunchDarklyIntegration()

    # Client not initialized.
    client = LDClient(config=Config("sdk-key"))
    monkeypatch.setattr(client, "is_initialized", lambda: False)
    with pytest.raises(DidNotEnable):
        LaunchDarklyIntegration(ld_client=client)


@pytest.mark.parametrize(
    "use_global_client",
    (False, True),
)
def test_launchdarkly_span_integration(
    sentry_init, use_global_client, capture_events, uninstall_integration
):
    td = TestData.data_source()
    td.update(td.flag("hello").variation_for_all(True))
    # Disable background requests as we aren't using a server.
    config = Config(
        "sdk-key", update_processor_class=td, diagnostic_opt_out=True, send_events=False
    )

    uninstall_integration(LaunchDarklyIntegration.identifier)
    if use_global_client:
        ldclient.set_config(config)
        sentry_init(traces_sample_rate=1, integrations=[LaunchDarklyIntegration()])
        client = ldclient.get()
    else:
        client = LDClient(config=config)
        sentry_init(
            traces_sample_rate=1,
            integrations=[LaunchDarklyIntegration(ld_client=client)],
        )

    events = capture_events()

    with start_transaction(name="hi"):
        with start_span(op="foo", name="bar"):
            client.variation("hello", Context.create("my-org", "organization"), False)
            client.variation("other", Context.create("my-org", "organization"), False)

    (event,) = events
    assert event["spans"][0]["data"] == ApproxDict(
        {"flag.hello": True, "flag.other": False}
    )
