from textwrap import dedent
from concurrent import futures
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

import sentry_sdk

from sentry_sdk.integrations.concurrent import ConcurrentIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

original_submit = ThreadPoolExecutor.submit
original_set_exception = Future.set_exception


@pytest.mark.parametrize("record_exceptions_on_futures", (True, False))
def test_handles_exceptions(sentry_init, capture_events, record_exceptions_on_futures):
    sentry_init(
        default_integrations=False,
        integrations=[
            ConcurrentIntegration(
                record_exceptions_on_futures=record_exceptions_on_futures
            )
        ],
    )
    events = capture_events()

    def crash():
        1 / 0

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(crash)
        with pytest.raises(ZeroDivisionError):
            future.result()

    if record_exceptions_on_futures:
        (event,) = events
        (exception,) = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"
        assert exception["mechanism"]["type"] == "concurrent"
        assert not exception["mechanism"]["handled"]
    else:
        assert not events


# ThreadPoolExecutor uses threading, but catches exceptions before the Sentry threading integration
@pytest.mark.parametrize(
    "potentially_conflicting_integrations",
    [
        [ThreadingIntegration(propagate_scope=True)],
        [ThreadingIntegration(propagate_scope=False)],
        [],
    ],
)
def test_threading_enabled_no_duplicate(
    sentry_init, capture_events, potentially_conflicting_integrations
):
    sentry_init(
        default_integrations=False,
        integrations=[
            ConcurrentIntegration(),
        ]
        + potentially_conflicting_integrations,
    )
    events = capture_events()

    def crash():
        1 / 0

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(crash)
        with pytest.raises(ZeroDivisionError):
            future.result()

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "concurrent"
    assert not exception["mechanism"]["handled"]


def test_concurrent_deduplicates(
    sentry_init, capture_events, capture_record_lost_event_calls
):
    sentry_init(
        default_integrations=False,
        integrations=[
            ExcepthookIntegration(),
            DedupeIntegration(),
            ConcurrentIntegration(record_exceptions_on_futures=True),
        ],
    )
    events = capture_events()
    record_lost_event_calls = capture_record_lost_event_calls()

    def crash():
        1 / 0

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(crash)
        try:
            future.result()
        except Exception:
            sentry_sdk.capture_exception()

    (event,) = events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    (lost_event_call,) = record_lost_event_calls
    assert lost_event_call == ("event_processor", "error", None, 1)


def test_propagates_tag(sentry_init, capture_events):
    sentry_init(
        default_integrations=False,
        integrations=[ConcurrentIntegration()],
    )
    events = capture_events()

    def stage1():
        sentry_sdk.get_isolation_scope().set_tag("stage1", "true")
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(stage2)
            with pytest.raises(ZeroDivisionError):
                future.result()

    def stage2():
        1 / 0

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(stage1)
        future.result()

    (event,) = events

    (exception,) = event["exception"]["values"]

    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "concurrent"
    assert not exception["mechanism"]["handled"]

    assert event["tags"]["stage1"] == "true"


def test_propagates_threadpool_scope(sentry_init, capture_events):
    sentry_init(
        default_integrations=False,
        traces_sample_rate=1.0,
        integrations=[ConcurrentIntegration()],
    )
    events = capture_events()

    def double(number):
        with sentry_sdk.start_span(op="task", name=str(number)):
            return number * 2

    with sentry_sdk.start_transaction(name="test_handles_threadpool"):
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            tasks = [executor.submit(double, number) for number in [1, 2, 3, 4]]
            for future in futures.as_completed(tasks):
                print("Getting future value!", future.result())

    sentry_sdk.flush()

    assert len(events) == 1
    (event,) = events
    assert event["spans"][0]["trace_id"] == event["spans"][1]["trace_id"]
    assert event["spans"][1]["trace_id"] == event["spans"][2]["trace_id"]
    assert event["spans"][2]["trace_id"] == event["spans"][3]["trace_id"]
    assert event["spans"][3]["trace_id"] == event["spans"][0]["trace_id"]


def test_propagates_threadpool_scope_in_map(sentry_init, capture_events):
    sentry_init(
        default_integrations=False,
        traces_sample_rate=1.0,
        integrations=[ConcurrentIntegration()],
    )
    events = capture_events()

    def double(number):
        with sentry_sdk.start_span(op="task", name=str(number)):
            return number * 2

    with sentry_sdk.start_transaction(name="test_handles_threadpool"):
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            for value in executor.map(double, [1, 2, 3, 4]):
                print("Getting future value!", value)

    sentry_sdk.flush()

    assert len(events) == 1
    (event,) = events
    assert event["spans"][0]["trace_id"] == event["spans"][1]["trace_id"]
    assert event["spans"][1]["trace_id"] == event["spans"][2]["trace_id"]
    assert event["spans"][2]["trace_id"] == event["spans"][3]["trace_id"]
    assert event["spans"][3]["trace_id"] == event["spans"][0]["trace_id"]


def test_double_patching(sentry_init, capture_events):
    sentry_init(integrations=[ConcurrentIntegration()])
    events = capture_events()

    def run():
        1 / 0

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        for _ in range(10):
            executor.submit(run)

    assert len(events) == 10
    for event in events:
        (exception,) = event["exception"]["values"]
        assert exception["type"] == "ZeroDivisionError"


def test_scope_data_not_leaked_in_executor(sentry_init):
    sentry_init(
        integrations=[ConcurrentIntegration()],
    )

    sentry_sdk.set_tag("initial_tag", "initial_value")
    initial_iso_scope = sentry_sdk.get_isolation_scope()

    def do_some_work():
        # check if we have the initial scope data propagated into the thread
        assert sentry_sdk.get_isolation_scope()._tags == {
            "initial_tag": "initial_value"
        }

        # change data in isolation scope in thread
        sentry_sdk.set_tag("thread_tag", "thread_value")

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(do_some_work)
        future.result()

    # check if the initial scope data is not modified by the started thread
    assert initial_iso_scope._tags == {
        "initial_tag": "initial_value"
    }, "The isolation scope in the main thread should not be modified by the started thread."


def test_spans_from_multiple_threads(sentry_init, capture_events, render_span_tree):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ConcurrentIntegration()],
    )
    events = capture_events()

    def do_some_work(number):
        with sentry_sdk.start_span(
            op=f"inner-run-{number}", name=f"Thread: child-{number}"
        ):
            pass

    with sentry_sdk.start_transaction(op="outer-trx"):
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            for number in range(5):
                with sentry_sdk.start_span(
                    op=f"outer-submit-{number}", name="Thread: main"
                ):
                    future = executor.submit(do_some_work, number)
                    future.result()

    (event,) = events

    assert render_span_tree(event) == dedent(
        """\
            - op="outer-trx": description=null
              - op="outer-submit-0": description="Thread: main"
                - op="inner-run-0": description="Thread: child-0"
              - op="outer-submit-1": description="Thread: main"
                - op="inner-run-1": description="Thread: child-1"
              - op="outer-submit-2": description="Thread: main"
                - op="inner-run-2": description="Thread: child-2"
              - op="outer-submit-3": description="Thread: main"
                - op="inner-run-3": description="Thread: child-3"
              - op="outer-submit-4": description="Thread: main"
                - op="inner-run-4": description="Thread: child-4"\
"""
    )
