import pytest

from celery.contrib.testing.worker import start_worker

from sentry_sdk.utils import logger

from tests.integrations.celery.integration_tests import run_beat


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

    assert len(envelopes) == 2
    (envelop_in_progress, envelope_error) = envelopes

    check_in = envelop_in_progress.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "failure_from_beat"
    assert check_in["status"] == "in_progress"

    check_in = envelope_error.items[0].payload.json
    assert check_in["type"] == "check_in"
    assert check_in["monitor_slug"] == "failure_from_beat"
    assert check_in["status"] == "error"
