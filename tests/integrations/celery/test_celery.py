import threading
import kombu
from unittest import mock

import pytest
from celery import Celery, VERSION
from celery.bin import worker
from celery.app.task import Task
from opentelemetry import trace as otel_trace, context

import sentry_sdk
from sentry_sdk import get_current_span
from sentry_sdk.integrations.celery import (
    CeleryIntegration,
    _wrap_task_run,
)
from sentry_sdk.integrations.celery.beat import _get_headers
from tests.conftest import ApproxDict


@pytest.fixture
def connect_signal(request):
    def inner(signal, f):
        signal.connect(f)
        request.addfinalizer(lambda: signal.disconnect(f))

    return inner


@pytest.fixture
def init_celery(sentry_init, request):
    def inner(
        propagate_traces=True,
        backend="always_eager",
        monitor_beat_tasks=False,
        **kwargs,
    ):
        sentry_init(
            integrations=[
                CeleryIntegration(
                    propagate_traces=propagate_traces,
                    monitor_beat_tasks=monitor_beat_tasks,
                )
            ],
            **kwargs,
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

    with sentry_sdk.start_span(op="unit test transaction") as root_span:
        celery_invocation(dummy_task, 1, 2)
        _, expected_context = celery_invocation(dummy_task, 1, 0)

    (_, error_event, _, _) = events

    assert error_event["contexts"]["trace"]["trace_id"] == root_span.trace_id
    assert error_event["contexts"]["trace"]["span_id"] != root_span.span_id
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

    scope = sentry_sdk.get_isolation_scope()

    celery_invocation(dummy_task, 1, 2)
    _, expected_context = celery_invocation(dummy_task, 1, 0)

    (error_event,) = events

    assert (
        error_event["contexts"]["trace"]["trace_id"]
        == scope._propagation_context.trace_id
    )
    assert (
        error_event["contexts"]["trace"]["span_id"]
        != scope._propagation_context.span_id
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

    events = capture_events()

    with sentry_sdk.start_span(name="submission") as root_span:
        celery_invocation(dummy_task, 1, 0 if task_fails else 1)

    if task_fails:
        error_event = events.pop(0)
        assert error_event["contexts"]["trace"]["trace_id"] == root_span.trace_id
        assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"

    execution_event, submission_event = events
    assert execution_event["transaction"] == "dummy_task"
    assert execution_event["transaction_info"] == {"source": "task"}

    assert submission_event["transaction"] == "submission"
    assert submission_event["transaction_info"] == {"source": "custom"}

    assert execution_event["type"] == submission_event["type"] == "transaction"
    assert execution_event["contexts"]["trace"]["trace_id"] == root_span.trace_id
    assert submission_event["contexts"]["trace"]["trace_id"] == root_span.trace_id

    if task_fails:
        assert execution_event["contexts"]["trace"]["status"] == "internal_error"
    else:
        assert execution_event["contexts"]["trace"]["status"] == "ok"

    assert len(execution_event["spans"]) == 1
    assert execution_event["spans"][0] == ApproxDict(
        {
            "trace_id": str(root_span.trace_id),
            "op": "queue.process",
            "description": "dummy_task",
        }
    )
    assert submission_event["spans"] == [
        {
            "data": {
                "sentry.name": "dummy_task",
                "sentry.op": "queue.submit.celery",
                "sentry.origin": "auto.queue.celery",
                "sentry.source": "custom",
                "thread.id": mock.ANY,
                "thread.name": mock.ANY,
            },
            "description": "dummy_task",
            "op": "queue.submit.celery",
            "origin": "auto.queue.celery",
            "parent_span_id": submission_event["contexts"]["trace"]["span_id"],
            "span_id": submission_event["spans"][0]["span_id"],
            "start_timestamp": submission_event["spans"][0]["start_timestamp"],
            "timestamp": submission_event["spans"][0]["timestamp"],
            "trace_id": str(root_span.trace_id),
            "status": "ok",
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
        sentry_sdk.get_isolation_scope().set_tag("foo", "bar")
        results.append(42)

    for _ in range(10000):
        dummy_task.delay()

    assert results == [42] * 10000
    assert not sentry_sdk.get_isolation_scope()._tags


def test_simple_no_propagation(capture_events, init_celery):
    celery = init_celery(propagate_traces=False)
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task():
        1 / 0

    with sentry_sdk.start_span(name="task") as root_span:
        dummy_task.delay()

    (event,) = events
    assert event["contexts"]["trace"]["trace_id"] != root_span.trace_id
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


@pytest.mark.skip(
    reason="This test is hanging when running test with `tox --parallel auto`. TODO: Figure out why and fix it!"
)
@pytest.mark.forked
def test_redis_backend_trace_propagation(init_celery, capture_events_forksafe):
    celery = init_celery(traces_sample_rate=1.0, backend="redis")

    events = capture_events_forksafe()

    runs = []

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self):
        runs.append(1)
        1 / 0

    with sentry_sdk.start_span(name="submit_celery"):
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
        try:
            # older newrelic versions
            from newrelic.hooks.application_celery import (
                instrument_celery_execute_trace,
            )
            import celery.app.trace as celery_trace_module

            assert hasattr(celery_trace_module, "build_tracer")
            instrument_celery_execute_trace(celery_trace_module)

        except ImportError:
            # newer newrelic versions
            from newrelic.hooks.application_celery import instrument_celery_app_base
            import celery.app as celery_app_module

            assert hasattr(celery_app_module, "Celery")
            assert hasattr(celery_app_module.Celery, "send_task")
            instrument_celery_app_base(celery_app_module)

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
    init_celery, celery_invocation
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

    sampling_context = traces_sampler.call_args_list[0][0][0]
    assert sampling_context["celery.job.task"] == "dog_walk"
    for i, arg in enumerate(args_kwargs["args"]):
        assert sampling_context[f"celery.job.args.{i}"] == str(arg)
    for kwarg, value in args_kwargs["kwargs"].items():
        assert sampling_context[f"celery.job.kwargs.{kwarg}"] == str(value)


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

    with sentry_sdk.start_span(name="celery"):
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

    expected_headers = sentry_crons_setup.copy()
    # Newly added headers
    expected_headers["sentry-trace"] = mock.ANY
    expected_headers["baggage"] = mock.ANY
    expected_headers["sentry-task-enqueued-time"] = mock.ANY

    assert result.get() == expected_headers


def test_baggage_propagation(init_celery):
    celery = init_celery(traces_sample_rate=1.0, release="abcdef")

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, x, y):
        return _get_headers(self)

    with mock.patch("sentry_sdk.tracing_utils.Random.uniform", return_value=0.5):
        with sentry_sdk.start_span(name="task") as root_span:
            result = dummy_task.apply_async(
                args=(1, 0),
                headers={"baggage": "custom=value"},
            ).get()

            assert sorted(result["baggage"].split(",")) == sorted(
                [
                    "sentry-release=abcdef",
                    "sentry-trace_id={}".format(root_span.trace_id),
                    "sentry-transaction=task",
                    "sentry-environment=production",
                    "sentry-sample_rand=0.500000",
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

    # Since we're applying the task inline eagerly,
    # we need to cleanup the otel context for this test.
    # and since we patch build_tracer, we need to do this before that runs...
    # TODO: the right way is to not test this inline
    original_apply = Task.apply

    def cleaned_apply(*args, **kwargs):
        token = context.attach(otel_trace.set_span_in_context(otel_trace.INVALID_SPAN))
        rv = original_apply(*args, **kwargs)
        context.detach(token)
        return rv

    Task.apply = cleaned_apply

    @celery.task(name="dummy_task", bind=True)
    def dummy_task(self, message):
        trace_id = get_current_span().trace_id
        return trace_id

    with sentry_sdk.start_span(name="task") as root_span:
        root_span_trace_id = root_span.trace_id

        # should propagate trace
        task_trace_id = dummy_task.apply_async(
            args=("some message",),
        ).get()
        assert root_span_trace_id == task_trace_id, "Trace should be propagated"

        # should NOT propagate trace (overrides `propagate_traces` parameter in integration constructor)
        task_trace_id = dummy_task.apply_async(
            args=("another message",),
            headers={"sentry-propagate-traces": False},
        ).get()
        assert root_span_trace_id != task_trace_id, "Trace should NOT be propagated"

    Task.apply = original_apply


def test_apply_async_manually_span(sentry_init):
    sentry_init(
        integrations=[CeleryIntegration()],
    )

    def dummy_function(*args, **kwargs):
        headers = kwargs.get("headers")
        assert "sentry-trace" in headers
        assert "baggage" in headers

    wrapped = _wrap_task_run(dummy_function)
    wrapped(mock.MagicMock(), (), headers={})


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


@pytest.mark.parametrize("routing_key", ("celery", "custom"))
@mock.patch("celery.app.task.Task.request")
def test_messaging_destination_name_default_exchange(
    mock_request, routing_key, init_celery, capture_events
):
    celery_app = init_celery(traces_sample_rate=1.0)
    events = capture_events()
    mock_request.delivery_info = {"routing_key": routing_key, "exchange": ""}

    @celery_app.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert span["data"]["messaging.destination.name"] == routing_key


@mock.patch("celery.app.task.Task.request")
def test_messaging_destination_name_nondefault_exchange(
    mock_request, init_celery, capture_events
):
    """
    Currently, we only capture the routing key as the messaging.destination.name when
    we are using the default exchange (""). This is because the default exchange ensures
    that the routing key is the queue name. Other exchanges may not guarantee this
    behavior.
    """
    celery_app = init_celery(traces_sample_rate=1.0)
    events = capture_events()
    mock_request.delivery_info = {"routing_key": "celery", "exchange": "custom"}

    @celery_app.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert "messaging.destination.name" not in span["data"]


def test_messaging_id(init_celery, capture_events):
    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    @celery.task
    def example_task(): ...

    example_task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert "messaging.message.id" in span["data"]


def test_retry_count_zero(init_celery, capture_events):
    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    @celery.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert span["data"]["messaging.message.retry.count"] == 0


@mock.patch("celery.app.task.Task.request")
def test_retry_count_nonzero(mock_request, init_celery, capture_events):
    mock_request.retries = 3

    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    @celery.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert span["data"]["messaging.message.retry.count"] == 3


@pytest.mark.parametrize("system", ("redis", "amqp"))
def test_messaging_system(system, init_celery, capture_events):
    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    # Does not need to be a real URL, since we use always eager
    celery.conf.broker_url = f"{system}://example.com"  # noqa: E231

    @celery.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert span["data"]["messaging.system"] == system


@pytest.mark.parametrize("system", ("amqp", "redis"))
def test_producer_span_data(system, monkeypatch, sentry_init, capture_events):
    old_publish = kombu.messaging.Producer._publish

    def publish(*args, **kwargs):
        pass

    monkeypatch.setattr(kombu.messaging.Producer, "_publish", publish)

    sentry_init(integrations=[CeleryIntegration()], traces_sample_rate=1.0)
    celery = Celery(__name__, broker=f"{system}://example.com")  # noqa: E231
    events = capture_events()

    @celery.task()
    def task(): ...

    with sentry_sdk.start_span(name="task"):
        task.apply_async()

    (event,) = events
    span = next(span for span in event["spans"] if span["op"] == "queue.publish")

    assert span["data"]["messaging.system"] == system

    assert span["data"]["messaging.destination.name"] == "celery"
    assert "messaging.message.id" in span["data"]
    assert span["data"]["messaging.message.retry.count"] == 0

    monkeypatch.setattr(kombu.messaging.Producer, "_publish", old_publish)


def test_receive_latency(init_celery, capture_events):
    celery = init_celery(traces_sample_rate=1.0)
    events = capture_events()

    @celery.task()
    def task(): ...

    task.apply_async()

    (event,) = events
    (span,) = event["spans"]
    assert "messaging.message.receive.latency" in span["data"]
    assert span["data"]["messaging.message.receive.latency"] > 0


def tests_span_origin_consumer(init_celery, capture_events):
    celery = init_celery(traces_sample_rate=1.0)
    celery.conf.broker_url = "redis://example.com"  # noqa: E231

    events = capture_events()

    @celery.task()
    def task(): ...

    task.apply_async()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "auto.queue.celery"
    assert event["spans"][0]["origin"] == "auto.queue.celery"


def tests_span_origin_producer(monkeypatch, sentry_init, capture_events):
    old_publish = kombu.messaging.Producer._publish

    def publish(*args, **kwargs):
        pass

    monkeypatch.setattr(kombu.messaging.Producer, "_publish", publish)

    sentry_init(integrations=[CeleryIntegration()], traces_sample_rate=1.0)
    celery = Celery(__name__, broker="redis://example.com")  # noqa: E231

    events = capture_events()

    @celery.task()
    def task(): ...

    with sentry_sdk.start_span(name="custom_transaction"):
        task.apply_async()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"

    for span in event["spans"]:
        assert span["origin"] == "auto.queue.celery"

    monkeypatch.setattr(kombu.messaging.Producer, "_publish", old_publish)


@pytest.mark.forked
@mock.patch("celery.Celery.send_task")
def test_send_task_wrapped(
    patched_send_task,
    sentry_init,
    capture_events,
    reset_integrations,
):
    sentry_init(integrations=[CeleryIntegration()], traces_sample_rate=1.0)
    celery = Celery(__name__, broker="redis://example.com")  # noqa: E231

    events = capture_events()

    with sentry_sdk.start_span(name="custom_transaction"):
        celery.send_task("very_creative_task_name", args=(1, 2), kwargs={"foo": "bar"})

    (call,) = patched_send_task.call_args_list  # We should have exactly one call
    (args, kwargs) = call

    assert args == (celery, "very_creative_task_name")
    assert kwargs["args"] == (1, 2)
    assert kwargs["kwargs"] == {"foo": "bar"}
    assert set(kwargs["headers"].keys()) == {
        "sentry-task-enqueued-time",
        "sentry-trace",
        "baggage",
        "headers",
    }
    assert set(kwargs["headers"]["headers"].keys()) == {
        "sentry-trace",
        "baggage",
        "sentry-task-enqueued-time",
    }
    assert (
        kwargs["headers"]["sentry-trace"]
        == kwargs["headers"]["headers"]["sentry-trace"]
    )

    (event,) = events  # We should have exactly one event (the transaction)
    assert event["type"] == "transaction"
    assert event["transaction"] == "custom_transaction"

    (span,) = event["spans"]  # We should have exactly one span
    assert span["description"] == "very_creative_task_name"
    assert span["op"] == "queue.submit.celery"
    assert span["trace_id"] == kwargs["headers"]["sentry-trace"].split("-")[0]


@pytest.mark.skip(reason="placeholder so that forked test does not come last")
def test_placeholder():
    """Forked tests must not come last in the module.
    See https://github.com/pytest-dev/pytest-forked/issues/67#issuecomment-1964718720.
    """
    pass
