import pytest

import sentry_sdk
from sentry_sdk.spotlight import DEFAULT_SPOTLIGHT_URL


@pytest.fixture
def capture_spotlight_envelopes(monkeypatch):
    def inner():
        envelopes = []
        test_spotlight = sentry_sdk.get_client().spotlight
        old_capture_envelope = test_spotlight.capture_envelope

        def append_envelope(envelope):
            envelopes.append(envelope)
            return old_capture_envelope(envelope)

        monkeypatch.setattr(test_spotlight, "capture_envelope", append_envelope)
        return envelopes

    return inner


def test_spotlight_off_by_default(sentry_init):
    sentry_init()
    assert sentry_sdk.get_client().spotlight is None


def test_spotlight_default_url(sentry_init):
    sentry_init(spotlight=True)

    spotlight = sentry_sdk.get_client().spotlight
    assert spotlight is not None
    assert spotlight.url == "http://localhost:8969/stream"


def test_spotlight_custom_url(sentry_init):
    sentry_init(spotlight="http://foobar@test.com/132")

    spotlight = sentry_sdk.get_client().spotlight
    assert spotlight is not None
    assert spotlight.url == "http://foobar@test.com/132"


def test_spotlight_envelope(sentry_init, capture_spotlight_envelopes):
    sentry_init(spotlight=True)
    envelopes = capture_spotlight_envelopes()

    try:
        raise ValueError("aha!")
    except Exception:
        sentry_sdk.capture_exception()

    (envelope,) = envelopes
    payload = envelope.items[0].payload.json

    assert payload["exception"]["values"][0]["value"] == "aha!"


def test_spotlight_true_with_env_url_uses_env_url(sentry_init, monkeypatch):
    """Per spec: spotlight=True + env URL -> use env URL"""
    monkeypatch.setenv("SENTRY_SPOTLIGHT", "http://custom:9999/stream")
    sentry_init(spotlight=True)

    spotlight = sentry_sdk.get_client().spotlight
    assert spotlight is not None
    assert spotlight.url == "http://custom:9999/stream"


def test_spotlight_false_ignores_env_var(sentry_init, monkeypatch, caplog):
    """Per spec: spotlight=False ignores env var and logs warning"""
    import logging

    with caplog.at_level(logging.WARNING, logger="sentry_sdk.errors"):
        monkeypatch.setenv("SENTRY_SPOTLIGHT", "true")
        sentry_init(spotlight=False, debug=True)

        assert sentry_sdk.get_client().spotlight is None
        assert "ignoring SENTRY_SPOTLIGHT environment variable" in caplog.text


def test_spotlight_config_url_overrides_env_url_with_warning(
    sentry_init, monkeypatch, caplog
):
    """Per spec: config URL takes precedence over env URL with warning"""
    import logging

    with caplog.at_level(logging.WARNING, logger="sentry_sdk.errors"):
        monkeypatch.setenv("SENTRY_SPOTLIGHT", "http://env:9999/stream")
        sentry_init(spotlight="http://config:8888/stream", debug=True)

        spotlight = sentry_sdk.get_client().spotlight
        assert spotlight is not None
        assert spotlight.url == "http://config:8888/stream"
        assert "takes precedence over" in caplog.text


def test_spotlight_config_url_same_as_env_no_warning(sentry_init, monkeypatch, caplog):
    """No warning when config URL matches env URL"""
    import logging

    with caplog.at_level(logging.WARNING, logger="sentry_sdk.errors"):
        monkeypatch.setenv("SENTRY_SPOTLIGHT", "http://same:9999/stream")
        sentry_init(spotlight="http://same:9999/stream", debug=True)

        spotlight = sentry_sdk.get_client().spotlight
        assert spotlight is not None
        assert spotlight.url == "http://same:9999/stream"
        assert "takes precedence over" not in caplog.text


def test_spotlight_receives_session_envelopes(sentry_init, capture_spotlight_envelopes):
    """Spotlight should receive session envelopes, not just error events"""
    sentry_init(spotlight=True, release="test-release")
    envelopes = capture_spotlight_envelopes()

    # Start and end a session
    sentry_sdk.get_isolation_scope().start_session()
    sentry_sdk.get_isolation_scope().end_session()
    sentry_sdk.flush()

    # Should have received at least one envelope with session data
    assert len(envelopes) > 0
