import pytest

from celery import Celery

from sentry_sdk.integrations.celery import CeleryIntegration


@pytest.fixture()
def celery_config():
    return {
        "broker_url": "redis://127.0.0.1:6379/14",
        "result_backend": "redis://127.0.0.1:6379/15",
        "task_always_eager": False,
        "worker_concurrency": 1,
        "beat_scheduler": "tests.integrations.celery.integration_tests:ImmediateScheduler",
    }


@pytest.fixture
def celery_init(sentry_init, celery_config):
    """
    Create a Sentry instrumented Celery app.
    """

    def inner(propagate_traces=True, monitor_beat_tasks=False, **kwargs):
        sentry_init(
            integrations=[
                CeleryIntegration(
                    propagate_traces=propagate_traces,
                    monitor_beat_tasks=monitor_beat_tasks,
                )
            ],
            **kwargs,
        )
        app = Celery("tasks")
        app.conf.update(celery_config)

        return app

    return inner
