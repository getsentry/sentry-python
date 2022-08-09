import pytest

from sentry_sdk.integrations.huey import HueyIntegration

from huey.api import RedisExpireHuey


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
