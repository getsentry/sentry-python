from unittest.mock import MagicMock

import pytest

from sentry_sdk.integrations.opentelemetry.integration import OpenTelemetryIntegration

try:
    import opentelemetry.instrumentation.asyncio  # noqa: F401

    # We actually expect all OTel instrumentation packages to be available, but
    # for simplicity we just check for one here.
    instrumentation_packages_installed = True
except ImportError:
    instrumentation_packages_installed = False


needs_potel = pytest.mark.skipif(
    not instrumentation_packages_installed,
    reason="needs OTel instrumentor libraries installed",
)


def test_integration_enabled_if_option_is_on(sentry_init, reset_integrations):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init(
        _experiments={
            "otel_powered_performance": True,
        },
    )
    OpenTelemetryIntegration.setup_once.assert_called_once()


def test_integration_not_enabled_if_option_is_off(sentry_init, reset_integrations):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init(
        _experiments={
            "otel_powered_performance": False,
        },
    )
    OpenTelemetryIntegration.setup_once.assert_not_called()


def test_integration_not_enabled_if_option_is_missing(sentry_init, reset_integrations):
    OpenTelemetryIntegration.setup_once = MagicMock()
    sentry_init()
    OpenTelemetryIntegration.setup_once.assert_not_called()


@needs_potel
def test_instrumentors_applied(sentry_init, reset_integrations):
    sentry_init(
        _experiments={
            "otel_powered_performance": True,
        },
    )

    assert False


@needs_potel
def test_post_patching(sentry_init, reset_integrations):
    from flask import Flask

    sentry_init(
        _experiments={
            "otel_powered_performance": True,
        },
        debug=True,
    )

    app = Flask(__name__)
    assert hasattr(app, "_is_instrumented_by_opentelemetry")
    assert app._is_instrumented_by_opentelemetry is True
