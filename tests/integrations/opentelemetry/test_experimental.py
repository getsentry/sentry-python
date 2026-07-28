from unittest.mock import MagicMock, patch

import pytest

from sentry_sdk.integrations.opentelemetry.integration import OpenTelemetryIntegration


@pytest.mark.forked
def test_integration_enabled_if_option_is_on(sentry_init, reset_integrations):
    mocked_setup_sentry_tracing = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration._setup_sentry_tracing",
        mocked_setup_sentry_tracing,
    ):
        with pytest.warns(DeprecationWarning):
            sentry_init(_experiments={"otel_powered_performance": True})
        mocked_setup_sentry_tracing.assert_called_once()


@pytest.mark.forked
def test_integration_not_enabled_if_option_is_off(sentry_init, reset_integrations):
    mocked_setup_sentry_tracing = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration._setup_sentry_tracing",
        mocked_setup_sentry_tracing,
    ):
        sentry_init(_experiments={"otel_powered_performance": False})
        mocked_setup_sentry_tracing.assert_not_called()


@pytest.mark.forked
def test_integration_not_enabled_if_option_is_missing(sentry_init, reset_integrations):
    mocked_setup_sentry_tracing = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration._setup_sentry_tracing",
        mocked_setup_sentry_tracing,
    ):
        sentry_init()
        mocked_setup_sentry_tracing.assert_not_called()


@pytest.mark.forked
def test_integration_disabled_with_span_streaming(sentry_init, reset_integrations):
    mocked_setup_sentry_tracing = MagicMock()

    with patch(
        "sentry_sdk.integrations.opentelemetry.integration._setup_sentry_tracing",
        mocked_setup_sentry_tracing,
    ):
        sentry_init(
            integrations=[OpenTelemetryIntegration()],
            trace_lifecycle="stream",
        )
        mocked_setup_sentry_tracing.assert_not_called()
