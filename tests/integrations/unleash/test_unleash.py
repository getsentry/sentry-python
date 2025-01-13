import concurrent.futures as cf
import sys
from random import random
from unittest import mock
from UnleashClient import UnleashClient

import pytest

import sentry_sdk
from sentry_sdk.integrations.unleash import UnleashIntegration
from tests.integrations.unleash.testutils import mock_unleash_client


def test_is_enabled(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])
        client.is_enabled("hello")
        client.is_enabled("world")
        client.is_enabled("other")

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": True},
            {"flag": "world", "result": False},
            {"flag": "other", "result": False},
        ]
    }


def test_get_variant(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore
        client.get_variant("no_payload_feature")
        client.get_variant("string_feature")
        client.get_variant("json_feature")
        client.get_variant("csv_feature")
        client.get_variant("number_feature")
        client.get_variant("unknown_feature")

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "no_payload_feature", "result": True},
            {"flag": "string_feature", "result": True},
            {"flag": "json_feature", "result": True},
            {"flag": "csv_feature", "result": True},
            {"flag": "number_feature", "result": True},
            {"flag": "unknown_feature", "result": False},
        ]
    }


def test_is_enabled_threaded(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore
        events = capture_events()

        def task(flag_key):
            # Creates a new isolation scope for the thread.
            # This means the evaluations in each task are captured separately.
            with sentry_sdk.isolation_scope():
                client.is_enabled(flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        # Capture an eval before we split isolation scopes.
        client.is_enabled("hello")

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


def test_get_variant_threaded(sentry_init, capture_events, uninstall_integration):
    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore
        events = capture_events()

        def task(flag_key):
            # Creates a new isolation scope for the thread.
            # This means the evaluations in each task are captured separately.
            with sentry_sdk.isolation_scope():
                client.get_variant(flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        # Capture an eval before we split isolation scopes.
        client.get_variant("hello")

        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            pool.map(task, ["no_payload_feature", "other"])

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
            {"flag": "no_payload_feature", "result": True},
        ]
    }
    assert events[2]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_is_enabled_asyncio(sentry_init, capture_events, uninstall_integration):
    asyncio = pytest.importorskip("asyncio")
    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore
        events = capture_events()

        async def task(flag_key):
            with sentry_sdk.isolation_scope():
                client.is_enabled(flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        async def runner():
            return asyncio.gather(task("world"), task("other"))

        # Capture an eval before we split isolation scopes.
        client.is_enabled("hello")

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


@pytest.mark.skipif(sys.version_info < (3, 7), reason="requires python3.7 or higher")
def test_get_variant_asyncio(sentry_init, capture_events, uninstall_integration):
    asyncio = pytest.importorskip("asyncio")

    uninstall_integration(UnleashIntegration.identifier)

    with mock_unleash_client():
        client = UnleashClient()
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore
        events = capture_events()

        async def task(flag_key):
            with sentry_sdk.isolation_scope():
                client.get_variant(flag_key)
                # use a tag to identify to identify events later on
                sentry_sdk.set_tag("task_id", flag_key)
                sentry_sdk.capture_exception(Exception("something wrong!"))

        async def runner():
            return asyncio.gather(task("no_payload_feature"), task("other"))

        # Capture an eval before we split isolation scopes.
        client.get_variant("hello")

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
            {"flag": "no_payload_feature", "result": True},
        ]
    }
    assert events[2]["contexts"]["flags"] == {
        "values": [
            {"flag": "hello", "result": False},
            {"flag": "other", "result": False},
        ]
    }


def test_wraps_original(sentry_init, uninstall_integration):
    with mock_unleash_client():
        client = UnleashClient()

        mock_is_enabled = mock.Mock(return_value=random() < 0.5)
        mock_get_variant = mock.Mock(return_value={"enabled": random() < 0.5})
        client.is_enabled = mock_is_enabled
        client.get_variant = mock_get_variant

        uninstall_integration(UnleashIntegration.identifier)
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore

    res = client.is_enabled("test-flag", "arg", kwarg=1)
    assert res == mock_is_enabled.return_value
    assert mock_is_enabled.call_args == (
        ("test-flag", "arg"),
        {"kwarg": 1},
    )

    res = client.get_variant("test-flag", "arg", kwarg=1)
    assert res == mock_get_variant.return_value
    assert mock_get_variant.call_args == (
        ("test-flag", "arg"),
        {"kwarg": 1},
    )


def test_wrapper_attributes(sentry_init, uninstall_integration):
    with mock_unleash_client():
        client = UnleashClient()  # <- Returns a MockUnleashClient

        original_is_enabled = client.is_enabled
        original_get_variant = client.get_variant

        uninstall_integration(UnleashIntegration.identifier)
        sentry_init(integrations=[UnleashIntegration()])  # type: ignore

        # Mock clients methods have not lost their qualified names after decoration.
        assert client.is_enabled.__name__ == "is_enabled"
        assert client.is_enabled.__qualname__ == original_is_enabled.__qualname__
        assert client.get_variant.__name__ == "get_variant"
        assert client.get_variant.__qualname__ == original_get_variant.__qualname__
