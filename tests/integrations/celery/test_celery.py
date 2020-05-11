import threading
import functools

import pytest

pytest.importorskip("celery")

from sentry_sdk import Hub, configure_scope
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk._compat import text_type

from celery import Celery, VERSION
from celery.bin import worker


@pytest.fixture
def connect_signal(request):
    def inner(signal, f):
        signal.connect(f)
        request.addfinalizer(lambda: signal.disconnect(f))

    return inner


@pytest.fixture
def init_celery(sentry_init):
    def inner(propagate_traces=True, **kwargs):
        sentry_init(
            integrations=[CeleryIntegration(propagate_traces=propagate_traces)],
            **kwargs
        )
        celery = Celery(__name__)
        if VERSION < (4,):
            celery.conf.CELERY_ALWAYS_EAGER = True
        else:
            celery.conf.task_always_eager = True
        return celery

    return inner


@pytest.fixture
def celery(init_celery):
    return init_celery()


@pytest.fixture(
    params=[
        lambda task, x, y: (task.delay(x, y), {"args": [x, y], "kwargs": {}}),
        lambda task, x, y: (task.apply_async((x, y)), {"args": [x, y], "kwargs": {}}),
        lambda task, x, y: (
            task.apply_async(args=(x, y)),
            {"args": [x, y], "kwargs": {}},
        ),
        lambda task, x, y: (
            task.apply_async(kwargs=dict(x=x, y=y)),
            {"args": [], "kwargs": {"x": x, "y": y}},
        ),
    ]
)
def celery_invocation(request):
    """
    Invokes a task in multiple ways Celery allows you to (testing our apply_async monkeypatch).

    Currently limited to a task signature of the form foo(x, y)
    """
    return request.param


def test_simple(capture_events, celery, celery_invocation):
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        foo = 42  # noqa
        return x / y

    with Hub.current.start_span() as span:
        celery_invocation(dummy_task, 1, 2)
        _, expected_context = celery_invocation(dummy_task, 1, 0)

    (event,) = events

    assert event["contexts"]["trace"]["trace_id"] == span.trace_id
    assert event["contexts"]["trace"]["span_id"] != span.span_id
    assert event["transaction"] == "dummy_task"
    assert "celery_task_id" in event["tags"]
    assert event["extra"]["celery-job"] == dict(
        task_name="dummy_task", **expected_context
    )

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "celery"
    assert exception["stacktrace"]["frames"][0]["vars"]["foo"] == "42"


@pytest.mark.parametrize("task_fails", [True, False], ids=["error", "success"])
def test_transaction_events(capture_events, init_celery, celery_invocation, task_fails):
    celery = init_celery(traces_sample_rate=1.0)

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        return x / y

    # XXX: For some reason the first call does not get instrumented properly.
    celery_invocation(dummy_task, 1, 1)

    events = capture_events()

    with Hub.current.start_span(transaction="submission") as span:
        celery_invocation(dummy_task, 1, 0 if task_fails else 1)

    if task_fails:
        error_event = events.pop(0)
        assert error_event["contexts"]["trace"]["trace_id"] == span.trace_id
        assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"

    execution_event, submission_event = events

    assert execution_event["transaction"] == "dummy_task"
    assert submission_event["transaction"] == "submission"

    assert execution_event["type"] == submission_event["type"] == "transaction"
    assert execution_event["contexts"]["trace"]["trace_id"] == span.trace_id
    assert submission_event["contexts"]["trace"]["trace_id"] == span.trace_id

    if task_fails:
        assert execution_event["contexts"]["trace"]["status"] == "internal_error"
    else:
        assert execution_event["contexts"]["trace"]["status"] == "ok"

    assert execution_event["spans"] == []
    assert submission_event["spans"] == [
        {
            u"description": u"dummy_task",
            u"op": "celery.submit",
            u"parent_span_id": submission_event["contexts"]["trace"]["span_id"],
            u"same_process_as_parent": True,
            u"span_id": submission_event["spans"][0]["span_id"],
            u"start_timestamp": submission_event["spans"][0]["start_timestamp"],
            u"timestamp": submission_event["spans"][0]["timestamp"],
            u"trace_id": text_type(span.trace_id),
        }
    ]


def test_no_stackoverflows(celery):
    """We used to have a bug in the Celery integration where its monkeypatching
    was repeated for every task invocation, leading to stackoverflows.

    See https://github.com/getsentry/sentry-python/issues/265
    """

    results = []

    @celery.task(name="dummy_task")
    def dummy_task():
        with configure_scope() as scope:
            scope.set_tag("foo", "bar")

        results.append(42)

    for _ in range(10000):
        dummy_task.delay()

    assert results == [42] * 10000

    with configure_scope() as scope:
        assert not scope._tags


def test_simple_no_propagation(capture_events, init_celery):
    celery = init_celery(propagate_traces=False)
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task():
        1 / 0

    with Hub.current.start_span() as span:
        dummy_task.delay()

    (event,) = events
    assert event["contexts"]["trace"]["trace_id"] != span.trace_id
    assert event["transaction"] == "dummy_task"
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"


def test_ignore_expected(capture_events, celery):
    events = capture_events()

    @celery.task(name="dummy_task", throws=(ZeroDivisionError,))
    def dummy_task(x, y):
        return x / y

    dummy_task.delay(1, 2)
    dummy_task.delay(1, 0)
    assert not events


def test_broken_prerun(init_celery, connect_signal):
    from celery.signals import task_prerun

    stack_lengths = []

    def crash(*args, **kwargs):
        # scope should exist in prerun
        stack_lengths.append(len(Hub.current._stack))
        1 / 0

    # Order here is important to reproduce the bug: In Celery 3, a crashing
    # prerun would prevent other preruns from running.

    connect_signal(task_prerun, crash)
    celery = init_celery()

    assert len(Hub.current._stack) == 1

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        stack_lengths.append(len(Hub.current._stack))
        return x / y

    if VERSION >= (4,):
        dummy_task.delay(2, 2)
    else:
        with pytest.raises(ZeroDivisionError):
            dummy_task.delay(2, 2)

    assert len(Hub.current._stack) == 1
    if VERSION < (4,):
        assert stack_lengths == [2]
    else:
        assert stack_lengths == [2, 2]


@pytest.mark.xfail(
    (4, 2, 0) <= VERSION,
    strict=True,
    reason="https://github.com/celery/celery/issues/4661",
)
def test_retry(celery, capture_events):
    events = capture_events()
    failures = [True, True, False]
    runs = []

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self):
        runs.append(1)
        try:
            if failures.pop(0):
                1 / 0
        except Exception as exc:
            self.retry(max_retries=2, exc=exc)

    dummy_task.delay()

    assert len(runs) == 3
    assert not events

    failures = [True, True, True]
    runs = []

    dummy_task.delay()

    assert len(runs) == 3
    (event,) = events
    exceptions = event["exception"]["values"]

    for e in exceptions:
        assert e["type"] == "ZeroDivisionError"


@pytest.mark.forked
@pytest.mark.skipif(VERSION < (4,), reason="in-memory backend broken")
def test_transport_shutdown(request, celery, capture_events_forksafe, tmpdir):
    events = capture_events_forksafe()

    celery.conf.worker_max_tasks_per_child = 1
    celery.conf.broker_url = "memory://localhost/"
    celery.conf.broker_backend = "memory"
    celery.conf.result_backend = "file://{}".format(tmpdir.mkdir("celery-results"))
    celery.conf.task_always_eager = False

    runs = []

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self):
        runs.append(1)
        1 / 0

    res = dummy_task.delay()

    w = worker.worker(app=celery)
    t = threading.Thread(target=w.run)
    t.daemon = True
    t.start()

    with pytest.raises(Exception):
        # Celery 4.1 raises a gibberish exception
        res.wait()

    event = events.read_event()
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    events.read_flush()

    # if this is nonempty, the worker never really forked
    assert not runs


@pytest.mark.forked
@pytest.mark.parametrize("newrelic_order", ["sentry_first", "sentry_last"])
def test_newrelic_interference(
    capture_events, init_celery, newrelic_order, celery_invocation
):
    def instrument_newrelic():
        import celery.app.trace as celery_mod
        from newrelic.hooks.application_celery import instrument_celery_execute_trace

        assert hasattr(celery_mod, "build_tracer")
        instrument_celery_execute_trace(celery_mod)

    if newrelic_order == "sentry_first":
        celery = init_celery()
        instrument_newrelic()
    elif newrelic_order == "sentry_last":
        instrument_newrelic()
        celery = init_celery()
    else:
        raise ValueError(newrelic_order)

    events = capture_events()

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, x, y):
        return x / y

    assert dummy_task.apply(kwargs={"x": 1, "y": 1}).wait() == 1
    assert celery_invocation(dummy_task, 1, 1)[0].wait() == 1
