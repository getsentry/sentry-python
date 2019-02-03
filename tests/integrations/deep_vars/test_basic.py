from sentry_sdk import capture_exception
from sentry_sdk.integrations.deep_vars import DeepVarsIntegration


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[DeepVarsIntegration()])
    events = capture_events()

    class Foo(object):
        y = 2

        @property
        def bogus(self):
            1 / 0

    try:
        foo = Foo()
        foo.x = 2

        1 / 0

    except Exception:
        capture_exception()

    event, = events

    exception, = event["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    frame, = exception["stacktrace"]["frames"]

    assert "Foo" in frame["vars"]["foo"]
    assert frame["vars"]["foo.x"] == "2"
