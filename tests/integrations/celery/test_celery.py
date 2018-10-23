import pytest

pytest.importorskip("celery")

from sentry_sdk.integrations.celery import CeleryIntegration

from celery import Celery


@pytest.fixture
def celery(sentry_init):
    sentry_init(integrations=[CeleryIntegration()])
    celery = Celery(__name__)
    celery.conf.CELERY_ALWAYS_EAGER = True
    return celery


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
    assert exception["stacktrace"]["frames"][1]["vars"]["foo"] == "42"


def test_ignore_expected(capture_events, celery):
    events = capture_events()

    @celery.task(name="dummy_task", throws=(ZeroDivisionError,))
    def dummy_task(x, y):
        return x / y

    dummy_task.delay(1, 2)
    dummy_task.delay(1, 0)
    assert not events
