import pytest

pytest.importorskip("celery")
pytest.importorskip("celery_once")

from celery_once import QueueOnce


class DummyBackend(object):
    def __init__(self, *args, **kwargs):
        pass

    def raise_or_lock(self, key, timeout):
        assert key == "qo_tests.integrations.celery.test_celery_once.dummy_task_x-1_y-1"
        assert timeout == 9001

    def clear_lock(self, key):
        assert key == "qo_tests.integrations.celery.test_celery_once.dummy_task_x-1_y-1"


@pytest.fixture
def celery_once(celery):
    celery.conf.ONCE = {
        "backend": "tests.integrations.celery.test_celery_once.DummyBackend",
        "settings": {"default_timeout": 9001},
    }


def test_celery_once(celery, celery_once):
    # Ensure dummy backend is call with correct key to set and clear locks.

    @celery.task(base=QueueOnce, once={"graceful": False})
    def dummy_task(x, y):
        pass

    dummy_task.delay(1, 1)
