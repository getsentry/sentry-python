import sentry_sdk

from sentry_sdk.integrations.modules import ModulesIntegration


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())

    (event,) = events
    assert "sentry-sdk" in event["modules"]
    assert "pytest" in event["modules"]
