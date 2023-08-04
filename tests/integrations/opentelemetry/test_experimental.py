try:
    # python 3.3 and above
    from unittest.mock import MagicMock
except ImportError:
    # python < 3.3
    from mock import MagicMock

from sentry_sdk.integrations.opentelemetry.integration import OpenTelemetryIntegration


def test_integration_enabled_if_option_is_on(sentry_init):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init(
        _experiments={
            "otel_powered_performance": True,
        }
    )
    OpenTelemetryIntegration.setup_once.assert_called_once()


def test_integration_not_enabled_if_option_is_off(sentry_init):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init(
        _experiments={
            "otel_powered_performance": False,
        }
    )
    OpenTelemetryIntegration.setup_once.assert_not_called()


def test_integration_not_enabled_if_option_is_missing(sentry_init):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init()
    OpenTelemetryIntegration.setup_once.assert_not_called()
