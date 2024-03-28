import threading

import pytest

from sentry_sdk import Hub, configure_scope, start_transaction, get_current_span
from sentry_sdk.integrations.celery import (
    CeleryIntegration,
    _get_headers,
    _wrap_apply_async,
)

from sentry_sdk._compat import text_type
from tests.conftest import ApproxDict

from celery import Celery, VERSION
from celery.bin import worker

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.fixture
def connect_signal(request):
    def inner(signal, f):
        signal.connect(f)
        request.addfinalizer(lambda: signal.disconnect(f))

    return inner


@pytest.fixture
def init_celery(sentry_init, request):
    def inner(propagate_traces=True, backend="always_eager", **kwargs):
        sentry_init(
            integrations=[CeleryIntegration(propagate_traces=propagate_traces)],
            **kwargs
        )
        celery = Celery(__name__)

        if backend == "always_eager":
            if VERSION < (4,):
                celery.conf.CELERY_ALWAYS_EAGER = True
            else:
                celery.conf.task_always_eager = True
        elif backend == "redis":
            # broken on celery 3
            if VERSION < (4,):
                pytest.skip("Redis backend broken for some reason")

            # this backend requires capture_events_forksafe
            celery.conf.worker_max_tasks_per_child = 1
            celery.conf.worker_concurrency = 1
            celery.conf.broker_url = "redis://127.0.0.1:6379"
            celery.conf.result_backend = "redis://127.0.0.1:6379"
            celery.conf.task_always_eager = False

            Hub.main.bind_client(Hub.current.client)
            request.addfinalizer(lambda: Hub.main.bind_client(None))

            # Once we drop celery 3 we can use the celery_worker fixture
            if VERSION < (5,):
                worker_fn = worker.worker(app=celery).run
            else:
                from celery.bin.base import CLIContext

                worker_fn = lambda: worker.worker(
                    obj=CLIContext(app=celery, no_color=True, workdir=".", quiet=False),
                    args=[],
                )

            worker_thread = threading.Thread(target=worker_fn)
            worker_thread.daemon = True
            worker_thread.start()
        else:
            raise ValueError(backend)

        return celery

    return inner


@pytest.fixture
def celery(init_celery):
    return init_celery()


@pytest.fixture(
    params=[
        lambda task, x, y: (
            task.delay(x, y),
            {"args": [x, y], "kwargs": {}},
        ),
        lambda task, x, y: (
            task.apply_async((x, y)),
            {"args": [x, y], "kwargs": {}},
        ),
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


def test_simple_with_performance(capture_events, init_celery, celery_invocation):
    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        foo = 42  # noqa
        return x / y

    with start_transaction(op="unit test transaction") as transaction:
        celery_invocation(dummy_task, 1, 2)
        _, expected_context = celery_invocation(dummy_task, 1, 0)

    (_, error_event, _, _) = events

    assert error_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert error_event["contexts"]["trace"]["span_id"] != transaction.span_id
    assert error_event["transaction"] == "dummy_task"
    assert "celery_task_id" in error_event["tags"]
    assert error_event["extra"]["celery-job"] == dict(
        task_name="dummy_task", **expected_context
    )

    (exception,) = error_event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "celery"
    assert exception["stacktrace"]["frames"][0]["vars"]["foo"] == "42"


def test_simple_without_performance(capture_events, init_celery, celery_invocation):
    celery = init_celery(traces_sample_rate=None)
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        foo = 42  # noqa
        return x / y

    with configure_scope() as scope:
        celery_invocation(dummy_task, 1, 2)
        _, expected_context = celery_invocation(dummy_task, 1, 0)

        (error_event,) = events

        assert (
            error_event["contexts"]["trace"]["trace_id"]
            == scope._propagation_context["trace_id"]
        )
        assert (
            error_event["contexts"]["trace"]["span_id"]
            != scope._propagation_context["span_id"]
        )
        assert error_event["transaction"] == "dummy_task"
        assert "celery_task_id" in error_event["tags"]
        assert error_event["extra"]["celery-job"] == dict(
            task_name="dummy_task", **expected_context
        )

        (exception,) = error_event["exception"]["values"]
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

    with start_transaction(name="submission") as transaction:
        celery_invocation(dummy_task, 1, 0 if task_fails else 1)

    if task_fails:
        error_event = events.pop(0)
        assert error_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
        assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"

    execution_event, submission_event = events
    assert execution_event["transaction"] == "dummy_task"
    assert execution_event["transaction_info"] == {"source": "task"}

    assert submission_event["transaction"] == "submission"
    assert submission_event["transaction_info"] == {"source": "custom"}

    assert execution_event["type"] == submission_event["type"] == "transaction"
    assert execution_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert submission_event["contexts"]["trace"]["trace_id"] == transaction.trace_id

    if task_fails:
        assert execution_event["contexts"]["trace"]["status"] == "internal_error"
    else:
        assert execution_event["contexts"]["trace"]["status"] == "ok"

    assert execution_event["spans"] == []
    assert submission_event["spans"] == [
        {
            "data": ApproxDict(),
            "description": "dummy_task",
            "op": "queue.submit.celery",
            "parent_span_id": submission_event["contexts"]["trace"]["span_id"],
            "same_process_as_parent": True,
            "span_id": submission_event["spans"][0]["span_id"],
            "start_timestamp": submission_event["spans"][0]["start_timestamp"],
            "timestamp": submission_event["spans"][0]["timestamp"],
            "trace_id": text_type(transaction.trace_id),
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

    with start_transaction() as transaction:
        dummy_task.delay()

    (event,) = events
    assert event["contexts"]["trace"]["trace_id"] != transaction.trace_id
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
    (4, 2, 0) <= VERSION < (4, 4, 3),
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


# TODO: This test is hanging when running test with `tox --parallel auto`. Find out why and fix it!
@pytest.mark.skip
@pytest.mark.forked
def test_redis_backend_trace_propagation(init_celery, capture_events_forksafe):
    celery = init_celery(traces_sample_rate=1.0, backend="redis", debug=True)

    events = capture_events_forksafe()

    runs = []

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self):
        runs.append(1)
        1 / 0

    with start_transaction(name="submit_celery"):
        # Curious: Cannot use delay() here or py2.7-celery-4.2 crashes
        res = dummy_task.apply_async()

    with pytest.raises(Exception):  # noqa: B017
        # Celery 4.1 raises a gibberish exception
        res.wait()

    # if this is nonempty, the worker never really forked
    assert not runs

    submit_transaction = events.read_event()
    assert submit_transaction["type"] == "transaction"
    assert submit_transaction["transaction"] == "submit_celery"

    assert len(
        submit_transaction["spans"]
    ), 4  # Because redis integration was auto enabled
    span = submit_transaction["spans"][0]
    assert span["op"] == "queue.submit.celery"
    assert span["description"] == "dummy_task"

    event = events.read_event()
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"

    transaction = events.read_event()
    assert (
        transaction["contexts"]["trace"]["trace_id"]
        == event["contexts"]["trace"]["trace_id"]
        == submit_transaction["contexts"]["trace"]["trace_id"]
    )

    events.read_flush()

    # if this is nonempty, the worker never really forked
    assert not runs


@pytest.mark.forked
@pytest.mark.parametrize("newrelic_order", ["sentry_first", "sentry_last"])
def test_newrelic_interference(init_celery, newrelic_order, celery_invocation):
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

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, x, y):
        return x / y

    assert dummy_task.apply(kwargs={"x": 1, "y": 1}).wait() == 1
    assert celery_invocation(dummy_task, 1, 1)[0].wait() == 1


def test_traces_sampler_gets_task_info_in_sampling_context(
    init_celery, celery_invocation, DictionaryContaining  # noqa:N803
):
    traces_sampler = mock.Mock()
    celery = init_celery(traces_sampler=traces_sampler)

    @celery.task(name="dog_walk")
    def walk_dogs(x, y):
        dogs, route = x
        num_loops = y
        return dogs, route, num_loops

    _, args_kwargs = celery_invocation(
        walk_dogs, [["Maisey", "Charlie", "Bodhi", "Cory"], "Dog park round trip"], 1
    )

    traces_sampler.assert_any_call(
        # depending on the iteration of celery_invocation, the data might be
        # passed as args or as kwargs, so make this generic
        DictionaryContaining({"celery_job": dict(task="dog_walk", **args_kwargs)})
    )


def test_abstract_task(capture_events, celery, celery_invocation):
    events = capture_events()

    class AbstractTask(celery.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            try:
                return self.run(*args, **kwargs)
            except ZeroDivisionError:
                return None

    @celery.task(name="dummy_task", base=AbstractTask)
    def dummy_task(x, y):
        return x / y

    with start_transaction():
        celery_invocation(dummy_task, 1, 0)

    assert not events


def test_task_headers(celery):
    """
    Test that the headers set in the Celery Beat auto-instrumentation are passed to the celery signal handlers
    """
    sentry_crons_setup = {
        "sentry-monitor-slug": "some-slug",
        "sentry-monitor-config": {"some": "config"},
        "sentry-monitor-check-in-id": "123abc",
    }

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, x, y):
        return _get_headers(self)

    # This is how the Celery Beat auto-instrumentation starts a task
    # in the monkey patched version of `apply_async`
    # in `sentry_sdk/integrations/celery.py::_wrap_apply_async()`
    result = dummy_task.apply_async(args=(1, 0), headers=sentry_crons_setup)
    assert result.get() == sentry_crons_setup


def test_baggage_propagation(init_celery):
    celery = init_celery(traces_sample_rate=1.0, release="abcdef")

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, x, y):
        return _get_headers(self)

    with start_transaction() as transaction:
        result = dummy_task.apply_async(
            args=(1, 0),
            headers={"baggage": "custom=value"},
        ).get()

        assert sorted(result["baggage"].split(",")) == sorted(
            [
                "sentry-release=abcdef",
                "sentry-trace_id={}".format(transaction.trace_id),
                "sentry-environment=production",
                "sentry-sample_rate=1.0",
                "sentry-sampled=true",
                "custom=value",
            ]
        )


def test_sentry_propagate_traces_override(init_celery):
    """
    Test if the `sentry-propagate-traces` header given to `apply_async`
    overrides the `propagate_traces` parameter in the integration constructor.
    """
    celery = init_celery(
        propagate_traces=True, traces_sample_rate=1.0, release="abcdef"
    )

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, message):
        trace_id = get_current_span().trace_id
        return trace_id

    with start_transaction() as transaction:
        transaction_trace_id = transaction.trace_id

        # should propagate trace
        task_transaction_id = dummy_task.apply_async(
            args=("some message",),
        ).get()
        assert transaction_trace_id == task_transaction_id

        # should NOT propagate trace (overrides `propagate_traces` parameter in integration constructor)
        task_transaction_id = dummy_task.apply_async(
            args=("another message",),
            headers={"sentry-propagate-traces": False},
        ).get()
        assert transaction_trace_id != task_transaction_id


def test_apply_async_manually_span(sentry_init):
    sentry_init(
        integrations=[CeleryIntegration()],
    )

    def dummy_function(*args, **kwargs):
        headers = kwargs.get("headers")
        assert "sentry-trace" in headers
        assert "baggage" in headers

    wrapped = _wrap_apply_async(dummy_function)
    wrapped(mock.MagicMock(), (), headers={})


def test_apply_async_from_beat_no_span(sentry_init):
    sentry_init(
        integrations=[CeleryIntegration()],
    )

    def dummy_function(*args, **kwargs):
        headers = kwargs.get("headers")
        assert "sentry-trace" not in headers
        assert "baggage" not in headers

    wrapped = _wrap_apply_async(dummy_function)
    wrapped(
        mock.MagicMock(),
        [
            "BEAT",
        ],
        headers={},
    )


def test_apply_async_no_args(init_celery):
    celery = init_celery()

    @celery.task
    def example_task():
        return "success"

    try:
        result = example_task.apply_async(None, {})
    except TypeError:
        pytest.fail("Calling `apply_async` without arguments raised a TypeError")

    assert result.get() == "success"
