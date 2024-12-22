from unittest.mock import patch

import sentry_sdk
from sentry_sdk.integrations.unleash import UnleashIntegration
from tests.integrations.unleash import MockUnleashClient

original_is_enabled = MockUnleashClient.is_enabled
original_get_variant = MockUnleashClient.get_variant


@patch("sentry_sdk.integrations.unleash.UnleashClient", MockUnleashClient)
def test_is_enabled(sentry_init, capture_events, reset_integration):
    mock_unleash_client = MockUnleashClient()
    reset_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration()])

    mock_unleash_client.is_enabled("hello")
    mock_unleash_client.is_enabled("world")
    mock_unleash_client.is_enabled("other")

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


@patch("sentry_sdk.integrations.unleash.UnleashClient", MockUnleashClient)
def test_get_variant(sentry_init, capture_events, reset_integration):
    mock_unleash_client = MockUnleashClient()
    reset_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration()])

    mock_unleash_client.get_variant("toggle_feature")
    mock_unleash_client.get_variant("string_feature")
    mock_unleash_client.get_variant("json_feature")
    mock_unleash_client.get_variant("csv_feature")
    mock_unleash_client.get_variant("number_feature")
    mock_unleash_client.get_variant("unknown_feature")

    events = capture_events()
    sentry_sdk.capture_exception(Exception("something wrong!"))

    assert len(events) == 1
    assert events[0]["contexts"]["flags"] == {
        "values": [
            {"flag": "toggle_feature", "result": True},
            {"flag": "unknown_feature", "result": False},
        ]
    }


def test_wrapper_attributes(sentry_init, reset_integration):
    reset_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration()])

    client = MockUnleashClient()
    assert client.is_enabled.__name__ == "is_enabled"
    assert client.is_enabled.__qualname__ == original_is_enabled.__qualname__
    assert MockUnleashClient.is_enabled.__name__ == "is_enabled"
    assert MockUnleashClient.is_enabled.__qualname__ == original_is_enabled.__qualname__

    assert client.get_variant.__name__ == "get_variant"
    assert client.get_variant.__qualname__ == original_get_variant.__qualname__
    assert MockUnleashClient.get_variant.__name__ == "get_variant"
    assert (
        MockUnleashClient.get_variant.__qualname__ == original_get_variant.__qualname__
    )
