from threading import Thread

import pytest

from sentry_sdk import configure_scope
from sentry_sdk.integrations.threading import ThreadingIntegration


@pytest.mark.parametrize("integrations", [[ThreadingIntegration()], []])
def test_handles_exceptions(sentry_init, capture_events, integrations):
    sentry_init(default_integrations=False, integrations=integrations)
    events = capture_events()

    def crash():
        1 / 0

    t = Thread(target=crash)
    t.start()
    t.join()

    if integrations:
        event, = events

        exception, = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"
        assert exception["mechanism"] == {"type": "threading", "handled": False}
    else:
        assert not events


@pytest.mark.parametrize("propagate_hub", (True, False))
def test_propagates_hub(sentry_init, capture_events, propagate_hub):
    sentry_init(
        default_integrations=False,
        integrations=[ThreadingIntegration(propagate_hub=propagate_hub)],
    )
    events = capture_events()

    def stage1():
        with configure_scope() as scope:
            scope.set_tag("stage1", True)

        t = Thread(target=stage2)
        t.start()
        t.join()

    def stage2():
        1 / 0

    t = Thread(target=stage1)
    t.start()
    t.join()

    event, = events

    exception, = event["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"] == {"type": "threading", "handled": False}

    if propagate_hub:
        assert event["tags"]["stage1"] is True
    else:
        assert "stage1" not in event.get("tags", {})
