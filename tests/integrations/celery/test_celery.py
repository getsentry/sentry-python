import pytest

pytest.importorskip("celery")

from sentry_sdk import Hub
from sentry_sdk.integrations.celery import CeleryIntegration

from celery import Celery, VERSION


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
        celery.conf.CELERY_ALWAYS_EAGER = True
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

    try:
        dummy_task.delay(2, 2)
    except ZeroDivisionError:
        if VERSION >= (4,):
            raise

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
