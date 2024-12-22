import pytest
from typing import Any
from unittest.mock import Mock

import sentry_sdk
from sentry_sdk.integrations.unleash import UnleashIntegration


@pytest.fixture
def mock_unleash_client():
    feature_to_variant = {
        "string_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "string", "value": "val1"},
        },
        "json_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "json", "value": '{"key1": 0.53}'},
        },
        "number_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "number", "value": "134.5"},
        },
        "csv_feature": {
            "name": "variant1",
            "enabled": True,
            "payload": {"type": "csv", "value": "abc 123\ncsbq 94"},
        },
        "toggle_feature": {"name": "variant1", "enabled": True},
    }

    disabled_variant = {"name": "disabled", "enabled": False}

    def get_variant(feature: str, *a, **kw) -> dict[str, Any]:
        return feature_to_variant.get(feature, disabled_variant)

    client = Mock()
    client.get_variant = get_variant
    return client


def test_get_variant(
    sentry_init, capture_events, uninstall_integration, mock_unleash_client
):
    uninstall_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration(mock_unleash_client)])

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
