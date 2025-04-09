import importlib
import os
from unittest.mock import patch

from opentelemetry import propagate
from sentry_sdk.propagator import SentryPropagator


def test_propagator_loaded_if_mentioned_in_environment_variable():
    try:
        with patch.dict(os.environ, {"OTEL_PROPAGATORS": "sentry"}):
            importlib.reload(propagate)

            assert len(propagate.propagators) == 1
            assert isinstance(propagate.propagators[0], SentryPropagator)
    finally:
        importlib.reload(propagate)
