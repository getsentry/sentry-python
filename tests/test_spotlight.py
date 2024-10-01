import pytest

import sentry_sdk


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
