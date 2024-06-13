from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from fastapi import FastAPI


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


@pytest.mark.forked
@needs_potel
def test_instrumentors_applied(sentry_init, reset_integrations):
    flask_instrument_mock = MagicMock()
    fastapi_instrument_mock = MagicMock()

    with patch(
        "opentelemetry.instrumentation.flask.FlaskInstrumentor.instrument",
        flask_instrument_mock,
    ):
        with patch(
            "opentelemetry.instrumentation.fastapi.FastAPIInstrumentor.instrument",
            fastapi_instrument_mock,
        ):
            sentry_init(
                _experiments={
                    "otel_powered_performance": True,
                },
            )

    flask_instrument_mock.assert_called_once()
    fastapi_instrument_mock.assert_called_once()


@pytest.mark.forked
@needs_potel
def test_post_patching(sentry_init, reset_integrations):
    assert not hasattr(
        Flask(__name__), "_is_instrumented_by_opentelemetry"
    ), "Flask is not patched at the start"
    assert not hasattr(
        FastAPI(), "_is_instrumented_by_opentelemetry"
    ), "FastAPI is not patched at the start"

    sentry_init(
        _experiments={
            "otel_powered_performance": True,
        },
    )

    flask = Flask(__name__)
    fastapi = FastAPI()

    assert hasattr(
        flask, "_is_instrumented_by_opentelemetry"
    ), "Flask has been patched after init()"
    assert flask._is_instrumented_by_opentelemetry is True

    assert hasattr(
        fastapi, "_is_instrumented_by_opentelemetry"
    ), "FastAPI has been patched after init()"
    assert fastapi._is_instrumented_by_opentelemetry is True
