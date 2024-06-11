import os
import signal
import threading
import time

from celery.beat import Scheduler

from sentry_sdk.utils import logger


class ImmediateScheduler(Scheduler):
    """
    A custom scheduler that starts tasks immediately after starting Celery beat.
    """

    def setup_schedule(self):
        super().setup_schedule()
        for _, entry in self.schedule.items():
            self.apply_entry(entry)

    def tick(self):
        # Override tick to prevent the normal schedule cycle
        return 1


def kill_beat(beat_pid_file, delay_seconds=1):
    """
    Terminates Celery Beat after the given `delay_seconds`.
    """
    logger.info("Starting Celery Beat killer...")
    time.sleep(delay_seconds)
    pid = int(open(beat_pid_file, "r").read())
    logger.info("Terminating Celery Beat...")
    os.kill(pid, signal.SIGTERM)


def run_beat(celery_app, loglevel="warning", quiet=True):
    """
    Run Celery Beat that immediately starts tasks.
    """
    logger.info("Starting Celery Beat...")
    pid_file = f"/tmp/celery-beat-{os.getpid()}.pid"
    t = threading.Thread(target=kill_beat, args=(pid_file,))
    t.start()
    beat_instance = celery_app.Beat(loglevel=loglevel, quiet=quiet, pidfile=pid_file)
    beat_instance.run()
