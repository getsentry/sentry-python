from importlib.metadata import entry_points

from sentry_sdk.integrations.opentelemetry import SentryPropagator


def test_propagator_registered_as_entry_point():
    eps = entry_points(group="opentelemetry_propagator", name="sentry")
    matches = list(eps)
    assert len(matches) == 1
    assert matches[0].value == "sentry_sdk.integrations.opentelemetry:SentryPropagator"

    loaded = matches[0].load()
    assert loaded is SentryPropagator
