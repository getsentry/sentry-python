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

    def crash(*args, **kwargs):
        1 / 0

    # Order here is important to reproduce the bug: In Celery 3, a crashing
    # prerun would prevent other preruns from running.

    connect_signal(task_prerun, crash)
    celery = init_celery()

    assert len(Hub.current._stack) == 1

    @celery.task(name="dummy_task")
    def dummy_task(x, y):
        assert len(Hub.current._stack) == 2
        return x / y

    try:
        dummy_task.delay(2, 2)
    except ZeroDivisionError:
        if VERSION >= (4,):
            raise

    assert len(Hub.current._stack) == 1
