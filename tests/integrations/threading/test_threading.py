import gc
from concurrent import futures
from textwrap import dedent
from threading import Thread
import sys

import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.threading import ThreadingIntegration
from sentry_sdk.tracing import _OTEL_VERSION

original_start = Thread.start
original_run = Thread.run


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


@pytest.mark.parametrize("propagate_scope", (True, False))
def test_propagates_scope(sentry_init, capture_events, propagate_scope):
    sentry_init(
        default_integrations=False,
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
    )
    events = capture_events()

    def stage1():
        sentry_sdk.get_isolation_scope().set_tag("stage1", "true")

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

    if propagate_scope:
        assert event["tags"]["stage1"] == "true"
    else:
        assert "stage1" not in event.get("tags", {})


@pytest.mark.parametrize("propagate_scope", (True, False))
def test_propagates_threadpool_scope(sentry_init, capture_events, propagate_scope):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
    )
    events = capture_events()

    def double(number):
        with sentry_sdk.start_span(op="task", name=str(number), only_if_parent=True):
            return number * 2

    with sentry_sdk.start_span(name="test_handles_threadpool"):
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            tasks = [executor.submit(double, number) for number in [1, 2, 3, 4]]
            for future in futures.as_completed(tasks):
                print("Getting future value!", future.result())

    sentry_sdk.flush()

    if propagate_scope:
        assert len(events) == 1
        (event,) = events
        assert event["spans"][0]["trace_id"] == event["spans"][1]["trace_id"]
        assert event["spans"][1]["trace_id"] == event["spans"][2]["trace_id"]
        assert event["spans"][2]["trace_id"] == event["spans"][3]["trace_id"]
        assert event["spans"][3]["trace_id"] == event["spans"][0]["trace_id"]
    else:
        (event,) = events
        assert len(event["spans"]) == 0


@pytest.mark.skipif(
    sys.version[:2] == [3, 8] and (1, 12) <= _OTEL_VERSION < (1, 15),
    reason="Fails in CI on 3.8 and specific OTel versions, works locally",
)
def test_circular_references(sentry_init, request):
    sentry_init(default_integrations=False, integrations=[ThreadingIntegration()])

    gc.disable()
    request.addfinalizer(gc.enable)

    gc.collect()

    class MyThread(Thread):
        def run(self):
            pass

    t = MyThread()
    t.start()
    t.join()
    del t

    unreachable_objects = gc.collect()
    assert unreachable_objects == 0


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


@pytest.mark.parametrize(
    "propagate_scope",
    (True, False),
    ids=["propagate_scope=True", "propagate_scope=False"],
)
def test_scope_data_not_leaked_in_threads(sentry_init, propagate_scope):
    sentry_init(
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
    )

    sentry_sdk.set_tag("initial_tag", "initial_value")
    initial_iso_scope = sentry_sdk.get_isolation_scope()

    def do_some_work():
        # check if we have the initial scope data propagated into the thread
        if propagate_scope:
            assert sentry_sdk.get_isolation_scope()._tags == {
                "initial_tag": "initial_value"
            }
        else:
            assert sentry_sdk.get_isolation_scope()._tags == {}

        # change data in isolation scope in thread
        sentry_sdk.set_tag("thread_tag", "thread_value")

    t = Thread(target=do_some_work)
    t.start()
    t.join()

    # check if the initial scope data is not modified by the started thread
    assert initial_iso_scope._tags == {
        "initial_tag": "initial_value"
    }, "The isolation scope in the main thread should not be modified by the started thread."


@pytest.mark.parametrize(
    "propagate_scope",
    (True, False),
    ids=["propagate_scope=True", "propagate_scope=False"],
)
def test_spans_from_multiple_threads(
    sentry_init, capture_events, render_span_tree, propagate_scope
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
    )
    events = capture_events()

    def do_some_work(number):
        with sentry_sdk.start_span(
            op=f"inner-run-{number}", name=f"Thread: child-{number}"
        ):
            pass

    threads = []

    with sentry_sdk.start_span(op="outer-trx"):
        for number in range(2):
            with sentry_sdk.start_span(
                op=f"outer-submit-{number}", name="Thread: main"
            ):
                t = Thread(target=do_some_work, args=(number,))
                t.start()
                threads.append(t)

        for t in threads:
            t.join()

    if propagate_scope:
        # The children spans from the threads become parts of the existing span
        # tree since we propagated the scope
        assert len(events) == 1
        (event,) = events

        assert render_span_tree(event) == dedent(
            """\
            - op="outer-trx": description=null
              - op="outer-submit-0": description="Thread: main"
                - op="inner-run-0": description="Thread: child-0"
              - op="outer-submit-1": description="Thread: main"
                - op="inner-run-1": description="Thread: child-1"\
"""
        )

    elif not propagate_scope:
        # The spans from the threads become their own root spans/transactions
        # as the connection to the parent span was severed when the scope was
        # cleared
        assert len(events) == 3
        (event1, event2, event3) = sorted(events, key=render_span_tree)

        assert render_span_tree(event1) == dedent(
            """\
            - op="inner-run-0": description=null\
"""
        )
        assert render_span_tree(event2) == dedent(
            """\
            - op="inner-run-1": description=null\
"""
        )

        assert render_span_tree(event3) == dedent(
            """\
            - op="outer-trx": description=null
              - op="outer-submit-0": description="Thread: main"
              - op="outer-submit-1": description="Thread: main"\
"""
        )
