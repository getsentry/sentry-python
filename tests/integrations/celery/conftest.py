import pytest


@pytest.fixture
def init_celery(sentry_init):
    pytest.importorskip("celery")

    from sentry_sdk.integrations.celery import CeleryIntegration
    from celery import Celery, VERSION

    def inner(propagate_traces=True, **kwargs):
        sentry_init(
            integrations=[CeleryIntegration(propagate_traces=propagate_traces)],
            **kwargs
        )
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
