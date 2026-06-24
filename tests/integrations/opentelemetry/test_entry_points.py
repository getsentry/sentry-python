try:
    from importlib.metadata import entry_points
except ImportError:
    from importlib_metadata import entry_points

from sentry_sdk.integrations.opentelemetry import SentryPropagator


def test_propagator_registered_as_entry_point():
    all_eps = entry_points()

    if isinstance(all_eps, dict):
        # Python 3.7-3.8 stdlib returns a dict keyed by group
        matches = [
            ep
            for ep in all_eps.get("opentelemetry_propagator", [])
            if ep.name == "sentry"
        ]
    else:
        matches = list(all_eps.select(group="opentelemetry_propagator", name="sentry"))

    assert len(matches) == 1
    assert matches[0].value == "sentry_sdk.integrations.opentelemetry:SentryPropagator"

    loaded = matches[0].load()
    assert loaded is SentryPropagator
