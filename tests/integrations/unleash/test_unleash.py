from typing import Any
from unittest.mock import Mock

import sentry_sdk
from sentry_sdk.integrations.unleash import UnleashIntegration

import pytest


@pytest.fixture
def mock_unleash_client():
    features = {
        "hello": True,
        "world": False,
    }

    def is_enabled(feature: str, *a, **kw) -> bool:
        return features.get(feature, False)

    feature_to_variant = {
        "str_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "string", "value": "val1"},
        },
        "json_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "json", "value": {"key1": 0.53}},
        },
        "toggle_feature": {"name": "variant1", "enabled": True},
    }

    disabled_variant = {"name": "disabled", "enabled": False}

    def get_variant(feature: str, *a, **kw) -> dict[str, Any]:
        return feature_to_variant.get(feature, disabled_variant)

    client = Mock()
    client.is_enabled = is_enabled
    client.get_variant = get_variant
    return client


def test_is_enabled(
    sentry_init, capture_events, uninstall_integration, mock_unleash_client
):
    uninstall_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration(mock_unleash_client)])

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


def test_get_variant(
    sentry_init, capture_events, uninstall_integration, mock_unleash_client
):
    uninstall_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration(mock_unleash_client)])

    mock_unleash_client.get_variant("toggle_feature")
    mock_unleash_client.get_variant("str_feature")
    mock_unleash_client.get_variant("json_feature")
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
