from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.forked
def test_integration_enabled_if_option_is_on(sentry_init, reset_integrations):
    mocked_setup_once = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration.OpenTelemetryIntegration.setup_once",
        mocked_setup_once,
    ):
        sentry_init(
            _experiments={
                "otel_powered_performance": True,
            },
        )
        mocked_setup_once.assert_called_once()


@pytest.mark.forked
def test_integration_not_enabled_if_option_is_off(sentry_init, reset_integrations):
    mocked_setup_once = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration.OpenTelemetryIntegration.setup_once",
        mocked_setup_once,
    ):
        sentry_init(
            _experiments={
                "otel_powered_performance": False,
            },
        )
        mocked_setup_once.assert_not_called()


@pytest.mark.forked
def test_integration_not_enabled_if_option_is_missing(sentry_init, reset_integrations):
    mocked_setup_once = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration.OpenTelemetryIntegration.setup_once",
        mocked_setup_once,
    ):
        sentry_init()
        mocked_setup_once.assert_not_called()
