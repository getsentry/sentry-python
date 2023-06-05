import gc

from threading import Thread

import pytest

from sentry_sdk import configure_scope, capture_message
from sentry_sdk.integrations.threading import ThreadingIntegration

original_start = Thread.start
original_run = Thread.run


@pytest.mark.forked
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
        (event,) = events

        (exception,) = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"
        assert exception["mechanism"]["type"] == "threading"
        assert not exception["mechanism"]["handled"]
    else:
        assert not events


@pytest.mark.forked
@pytest.mark.parametrize("propagate_hub", (True, False))
def test_propagates_hub(sentry_init, capture_events, propagate_hub):
    sentry_init(
        default_integrations=False,
        integrations=[ThreadingIntegration(propagate_hub=propagate_hub)],
    )
    events = capture_events()

    def stage1():
        with configure_scope() as scope:
            scope.set_tag("stage1", "true")

        t = Thread(target=stage2)
        t.start()
        t.join()

    def stage2():
        1 / 0

    t = Thread(target=stage1)
    t.start()
    t.join()

    (event,) = events

    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "threading"
    assert not exception["mechanism"]["handled"]

    if propagate_hub:
        assert event["tags"]["stage1"] == "true"
    else:
        assert "stage1" not in event.get("tags", {})


def test_circular_references(sentry_init, request):
    sentry_init(default_integrations=False, integrations=[ThreadingIntegration()])

    gc.collect()
    gc.disable()
    request.addfinalizer(gc.enable)

    class MyThread(Thread):
        def run(self):
            pass

    t = MyThread()
    t.start()
    t.join()
    del t

    assert not gc.collect()


@pytest.mark.forked
def test_double_patching(sentry_init, capture_events):
    sentry_init(default_integrations=False, integrations=[ThreadingIntegration()])
    events = capture_events()

    # XXX: Workaround for race condition in the py library's magic import
    # system (py is a dependency of pytest)
    capture_message("hi")
    del events[:]

    class MyThread(Thread):
        def run(self):
            1 / 0

    ts = []
    for _ in range(10):
        t = MyThread()
        t.start()
        ts.append(t)

    for t in ts:
        t.join()

    assert len(events) == 10
    for event in events:
        (exception,) = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"


def test_wrapper_attributes(sentry_init):
    sentry_init(default_integrations=False, integrations=[ThreadingIntegration()])

    def target():
        assert t.run.__name__ == "run"
        assert t.run.__qualname__ == original_run.__qualname__

    t = Thread(target=target)
    t.start()
    t.join()

    assert Thread.start.__name__ == "start"
    assert Thread.start.__qualname__ == original_start.__qualname__
    assert t.start.__name__ == "start"
    assert t.start.__qualname__ == original_start.__qualname__

    assert Thread.run.__name__ == "run"
    assert Thread.run.__qualname__ == original_run.__qualname__
    assert t.run.__name__ == "run"
    assert t.run.__qualname__ == original_run.__qualname__
