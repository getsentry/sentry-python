import os
import sys
import pytest

from celery.contrib.testing.worker import start_worker

from sentry_sdk.utils import logger

from tests.integrations.celery.integration_tests import run_beat


REDIS_SERVER = "redis://127.0.0.1:6379"
REDIS_DB = 15


@pytest.fixture()
def celery_config():
    return {
        "worker_concurrency": 1,
        "broker_url": f"{REDIS_SERVER}/{REDIS_DB}",
        "result_backend": f"{REDIS_SERVER}/{REDIS_DB}",
        "beat_scheduler": "tests.integrations.celery.integration_tests:ImmediateScheduler",
        "task_always_eager": False,
        "task_create_missing_queues": True,
        "task_default_queue": f"queue_{os.getpid()}",
    }


@pytest.fixture
def celery_init(sentry_init, celery_config):
    """
    Create a Sentry instrumented Celery app.
    """
    from celery import Celery

    from sentry_sdk.integrations.celery import CeleryIntegration

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


@pytest.mark.forked
@pytest.mark.skipif(sys.version_info < (3, 7), reason="Test requires Python 3.7+")
def test_explanation(celery_init, capture_envelopes):
    """
    This is a dummy test for explaining how to test using Celery Beat
    """

    # First initialize a Celery app.
    # You can give the options of CeleryIntegrations
    # and the options for `sentry_dks.init` as keyword arguments.
    # See the celery_init fixture for details.
    app = celery_init(
        monitor_beat_tasks=True,
    )

    # Capture envelopes.
    envelopes = capture_envelopes()

    # Define the task you want to run
    @app.task
    def test_task():
        logger.info("Running test_task")

    # Add the task to the beat schedule
    app.add_periodic_task(60.0, test_task.s(), name="success_from_beat")

    # Start a Celery worker
    with start_worker(app, perform_ping_check=False):
        # And start a Celery Beat instance
        # This Celery Beat will start the task above immediately
        # after start for the first time
        # By default Celery Beat is terminated after 1 second.
        # See `run_beat` function on how to change this.
        run_beat(app)

    # After the Celery Beat is terminated, you can check the envelopes
    assert len(envelopes) >= 0


@pytest.mark.forked
def test_beat_task_crons_success(celery_init, capture_envelopes):
    app = celery_init(
        monitor_beat_tasks=True,
    )
    envelopes = capture_envelopes()

    @app.task
    def test_task():
        logger.info("Running test_task")

    app.add_periodic_task(60.0, test_task.s(), name="success_from_beat")

    with start_worker(app, perform_ping_check=False):
        run_beat(app)

    assert len(envelopes) == 2
    (envelop_in_progress, envelope_ok) = envelopes

    assert envelop_in_progress.items[0].headers["type"] == "check_in"
    check_in = envelop_in_progress.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "success_from_beat"
    assert check_in["status"] == "in_progress"

    assert envelope_ok.items[0].headers["type"] == "check_in"
    check_in = envelope_ok.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "success_from_beat"
    assert check_in["status"] == "ok"


@pytest.mark.forked
def test_beat_task_crons_error(celery_init, capture_envelopes):
    app = celery_init(
        monitor_beat_tasks=True,
    )
    envelopes = capture_envelopes()

    @app.task
    def test_task():
        logger.info("Running test_task")
        1 / 0

    app.add_periodic_task(60.0, test_task.s(), name="failure_from_beat")

    with start_worker(app, perform_ping_check=False):
        run_beat(app)

    envelop_in_progress = envelopes[0]
    envelope_error = envelopes[-1]

    check_in = envelop_in_progress.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "failure_from_beat"
    assert check_in["status"] == "in_progress"

    check_in = envelope_error.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "failure_from_beat"
    assert check_in["status"] == "error"
