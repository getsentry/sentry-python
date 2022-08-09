import pytest
from decimal import DivisionByZero

from sentry_sdk.integrations.huey import HueyIntegration

from huey.api import RedisExpireHuey, Result
from huey.exceptions import RetryTask


@pytest.fixture
def init_huey(sentry_init):
    def inner():
        sentry_init(
            integrations=[HueyIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
            debug=True,
        )

        return RedisExpireHuey(name="sentry_sdk", url="redis://127.0.0.1:6379")

    return inner


@pytest.fixture(autouse=True)
def flush_huey_tasks(init_huey):
    huey = init_huey()
    huey.flush()


def execute_huey_task(huey, func, *args, exceptions=None, **kwargs):
    result = func(*args, **kwargs)
    task = huey.dequeue()
    if exceptions is not None:
        try:
            huey.execute(task)
        except exceptions:
            pass
    else:
        huey.execute(task)
    return result


def test_task_result(init_huey):
    huey = init_huey()

    @huey.task()
    def increase(num):
        return num + 1

    result = increase(3)

    assert isinstance(result, Result)
    assert len(huey) == 1
    task = huey.dequeue()
    assert huey.execute(task) == 4
    assert result.get() == 4


@pytest.mark.parametrize("task_fails", [True, False], ids=["error", "success"])
def test_task_transaction(capture_events, init_huey, task_fails):
    huey = init_huey()

    @huey.task()
    def division(a, b):
        return a / b

    events = capture_events()
    execute_huey_task(
        huey, division, 1, int(not task_fails), exceptions=(DivisionByZero,)
    )

    if task_fails:
        error_event = events.pop(0)
        assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert error_event["exception"]["values"][0]["mechanism"]["type"] == "huey"

    (event,) = events
    assert event["type"] == "transaction"
    assert event["transaction"] == "division"
    assert event["transaction_info"] == {"source": "task"}

    if task_fails:
        assert event["contexts"]["trace"]["status"] == "internal_error"
    else:
        assert event["contexts"]["trace"]["status"] == "ok"

    assert "huey_task_id" in event["tags"]
    assert "huey_task_retry" in event["tags"]


def test_task_retry(capture_events, init_huey):
    huey = init_huey()
    context = {"retry": True}

    @huey.task()
    def retry_task(context):
        if context["retry"]:
            context["retry"] = False
            raise RetryTask()

    events = capture_events()
    result = execute_huey_task(huey, retry_task, context)
    (event,) = events

    assert event["transaction"] == "retry_task"
    assert event["tags"]["huey_task_id"] == result.task.id
    assert len(huey) == 1

    task = huey.dequeue()
    huey.execute(task)
    (event, _) = events

    assert event["transaction"] == "retry_task"
    assert event["tags"]["huey_task_id"] == result.task.id
    assert len(huey) == 0
