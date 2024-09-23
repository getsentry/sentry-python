import sys

from sentry_sdk import capture_message
from sentry_sdk.integrations.argv import ArgvIntegration


def test_basic(sentry_init, capture_events, monkeypatch):
    sentry_init(integrations=[ArgvIntegration()])

    argv = ["foo", "bar", "baz"]
    monkeypatch.setattr(sys, "argv", argv)

    events = capture_events()
    capture_message("hi")
    (event,) = events
    assert event["extra"]["sys.argv"] == argv
