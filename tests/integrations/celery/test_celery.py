import threading

import pytest

pytest.importorskip("celery")

from sentry_sdk import Hub
from sentry_sdk.integrations.celery import CeleryIntegration

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
    def inner():
        sentry_init(integrations=[CeleryIntegration()])
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


def test_simple(capture_events, celery):
    events = capture_events()

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        foo = 42  # noqa
        return x / y

    dummy_task.delay(1, 2)
    dummy_task.delay(1, 0)
    event, = events
    assert event["transaction"] == "dummy_task"
    assert event["extra"]["celery-job"] == {
        "args": [1, 0],
        "kwargs": {},
        "task_name": "dummy_task",
    }

    exception, = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "celery"
    assert exception["stacktrace"]["frames"][0]["vars"]["foo"] == "42"


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


@pytest.mark.skipif(
    (4, 2, 0) <= VERSION < (4, 2, 2),
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
    event, = events
    exceptions = event["exception"]["values"]

    for e in exceptions:
        assert e["type"] == "ZeroDivisionError"


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
    exception, = event['exception']['values']
    assert exception['type'] == 'ZeroDivisionError'

    events.read_flush()

    # if this is nonempty, the worker never really forked
    assert not runs
