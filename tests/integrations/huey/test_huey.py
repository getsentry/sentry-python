import pytest

from sentry_sdk.integrations.huey import HueyIntegration

from huey.api import RedisExpireHuey, Result


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
