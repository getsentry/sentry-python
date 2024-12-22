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

    client = Mock()
    client.is_enabled = is_enabled
    return client


def test_unleash_integration(
    sentry_init, capture_events, uninstall_integration, mock_unleash_client
):
    uninstall_integration(UnleashIntegration.identifier)
    sentry_init(integrations=[UnleashIntegration(mock_unleash_client)])

    # Evaluate
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
