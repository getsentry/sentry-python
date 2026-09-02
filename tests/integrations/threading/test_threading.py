import sys
import time
from concurrent import futures
from textwrap import dedent
from threading import Thread

import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.threading import ThreadingIntegration

original_start = Thread.start
original_run = Thread.run


@pytest.mark.filterwarnings("ignore:.*:pytest.PytestUnhandledThreadExceptionWarning")
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


@pytest.mark.filterwarnings("ignore:.*:pytest.PytestUnhandledThreadExceptionWarning")
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
    assert initial_iso_scope._tags == {"initial_tag": "initial_value"}, (
        "The isolation scope in the main thread should not be modified by the started thread."
    )


@pytest.mark.parametrize(
    "propagate_scope",
    (True, False),
    ids=["propagate_scope=True", "propagate_scope=False"],
)
def test_spans_from_multiple_threads(
    sentry_init, capture_items, render_span_tree, propagate_scope
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    def do_some_work(number):
        with sentry_sdk.traces.start_span(
            name=f"Thread: child-{number}",
            attributes={"sentry.op": f"inner-run-{number}"},
        ):
            pass

    threads = []

    with sentry_sdk.traces.start_span(
        name="root span", attributes={"sentry.op": "outer-trx"}, parent_span=None
    ):
        for number in range(5):
            with sentry_sdk.traces.start_span(
                name="Thread: main",
                attributes={"sentry.op": f"outer-submit-{number}"},
            ):
                t = Thread(target=do_some_work, args=(number,))
                t.start()
                threads.append(t)

        for t in threads:
            t.join()

    time.sleep(0.1)
    sentry_sdk.flush()

    spans = [item.payload for item in items]

    # Free-threaded builds set thread_inherit_context to True, otherwise thread_inherit_context is False
    if propagate_scope or getattr(sys.flags, "thread_inherit_context", None):
        assert render_span_tree(spans) == dedent(
            """\
            - sentry.op="outer-trx": name="root span"
              - sentry.op="outer-submit-0": name="Thread: main"
                - sentry.op="inner-run-0": name="Thread: child-0"
              - sentry.op="outer-submit-1": name="Thread: main"
                - sentry.op="inner-run-1": name="Thread: child-1"
              - sentry.op="outer-submit-2": name="Thread: main"
                - sentry.op="inner-run-2": name="Thread: child-2"
              - sentry.op="outer-submit-3": name="Thread: main"
                - sentry.op="inner-run-3": name="Thread: child-3"
              - sentry.op="outer-submit-4": name="Thread: main"
                - sentry.op="inner-run-4": name="Thread: child-4"\
"""
        )

    elif not propagate_scope:
        assert render_span_tree(spans) == dedent(
            """\
            - sentry.op="outer-trx": name="root span"
              - sentry.op="outer-submit-0": name="Thread: main"
              - sentry.op="outer-submit-1": name="Thread: main"
              - sentry.op="outer-submit-2": name="Thread: main"
              - sentry.op="outer-submit-3": name="Thread: main"
              - sentry.op="outer-submit-4": name="Thread: main"\
"""
        )


@pytest.mark.parametrize(
    "propagate_scope",
    (True, False),
    ids=["propagate_scope=True", "propagate_scope=False"],
)
def test_spans_from_threadpool(
    sentry_init, capture_items, render_span_tree, propagate_scope
):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_scope=propagate_scope)],
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    def do_some_work(number):
        with sentry_sdk.traces.start_span(
            name=f"Thread: child-{number}",
            attributes={"sentry.op": f"inner-run-{number}"},
        ):
            pass

    with sentry_sdk.traces.start_span(
        name="root span", attributes={"sentry.op": "outer-trx"}, parent_span=None
    ):
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            for number in range(5):
                with sentry_sdk.traces.start_span(
                    name="Thread: main",
                    attributes={"sentry.op": f"outer-submit-{number}"},
                ):
                    future = executor.submit(do_some_work, number)
                    future.result()

    sentry_sdk.flush()

    spans = [item.payload for item in items]

    # Free-threaded builds set thread_inherit_context to True, otherwise thread_inherit_context is False
    if propagate_scope or getattr(sys.flags, "thread_inherit_context", None):
        assert render_span_tree(spans) == dedent(
            """\
            - sentry.op="outer-trx": name="root span"
              - sentry.op="outer-submit-0": name="Thread: main"
                - sentry.op="inner-run-0": name="Thread: child-0"
              - sentry.op="outer-submit-1": name="Thread: main"
                - sentry.op="inner-run-1": name="Thread: child-1"
              - sentry.op="outer-submit-2": name="Thread: main"
                - sentry.op="inner-run-2": name="Thread: child-2"
              - sentry.op="outer-submit-3": name="Thread: main"
                - sentry.op="inner-run-3": name="Thread: child-3"
              - sentry.op="outer-submit-4": name="Thread: main"
                - sentry.op="inner-run-4": name="Thread: child-4"\
"""
        )

    elif not propagate_scope:
        assert render_span_tree(spans) == dedent(
            """\
            - sentry.op="outer-trx": name="root span"
              - sentry.op="outer-submit-0": name="Thread: main"
              - sentry.op="outer-submit-1": name="Thread: main"
              - sentry.op="outer-submit-2": name="Thread: main"
              - sentry.op="outer-submit-3": name="Thread: main"
              - sentry.op="outer-submit-4": name="Thread: main"\
"""
        )
