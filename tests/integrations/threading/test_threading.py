import gc
from concurrent import futures
from threading import Thread

import pytest

import sentry_sdk
from sentry_sdk import capture_message
from sentry_sdk.integrations.threading import ThreadingIntegration

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


@pytest.mark.parametrize("propagate_hub", (True, False))
def test_propagates_hub(sentry_init, capture_events, propagate_hub):
    sentry_init(
        default_integrations=False,
        integrations=[ThreadingIntegration(propagate_hub=propagate_hub)],
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

    if propagate_hub:
        assert event["tags"]["stage1"] == "true"
    else:
        assert "stage1" not in event.get("tags", {})


@pytest.mark.parametrize("propagate_hub", (True, False))
def test_propagates_threadpool_hub(sentry_init, capture_events, propagate_hub):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_hub=propagate_hub)],
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

    if propagate_hub:
        assert len(events) == 1
        (event,) = events
        assert event["spans"][0]["trace_id"] == event["spans"][1]["trace_id"]
        assert event["spans"][1]["trace_id"] == event["spans"][2]["trace_id"]
        assert event["spans"][2]["trace_id"] == event["spans"][3]["trace_id"]
        assert event["spans"][3]["trace_id"] == event["spans"][0]["trace_id"]
    else:
        (event,) = events
        assert len(event["spans"]) == 0


@pytest.mark.skip(reason="Temporarily disable to release SDK 2.0a1.")
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


def test_trx_in_trx_not_working(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
        integrations=[ThreadingIntegration(propagate_hub=True)],
    )
    events = capture_events()

    def do_some_work(number):
        with sentry_sdk.start_transaction(name=f"inner-trx-{number}") as trx_inner:
            print(f"Inner Trx: {trx_inner}")
            with sentry_sdk.start_span(op=f"inner-{number}-a", name=str(number)):
                with sentry_sdk.start_span(op=f"inner-{number}-b", name=str(number)):
                    with sentry_sdk.start_span(
                        op=f"inner-{number}-c", name=str(number)
                    ):
                        with sentry_sdk.start_span(
                            op=f"inner-{number}-d", name=str(number)
                        ):
                            with sentry_sdk.start_span(
                                op=f"inner-{number}-e", name=str(number)
                            ):
                                with sentry_sdk.start_span(
                                    op=f"inner-{number}-f", name=str(number)
                                ):
                                    pass

    with sentry_sdk.start_transaction(name="outer-trx") as trx_outer:
        print(f"Outer Trx: {trx_outer}")
        with futures.ThreadPoolExecutor(max_workers=2) as executor:
            for number in range(20):
                with sentry_sdk.start_span(
                    op=f"outer1-{number}", name=str(number)
                ) as span:
                    with sentry_sdk.start_span(
                        op=f"outer2-{number}", name=str(number)
                    ) as span:
                        with sentry_sdk.start_span(
                            op=f"outer3-{number}", name=str(number)
                        ) as span:
                            executor.submit(do_some_work, number)

    wrong_span_found_in_trx = False
    for event in events:
        prefix = "inner" if event["transaction"].startswith("inner") else "outer"
        for span in event["spans"]:
            if not span["op"].startswith(prefix):
                wrong_span_found_in_trx = True
                break

    assert (
        wrong_span_found_in_trx
    ), "No wrong span found in transaction. It is excepted that there is one. (as we are testing a race condition, this test might fail randomly)"
