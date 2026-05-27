import os
import signal
import tempfile
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


def kill_beat(beat_pid_file, delay_seconds=1, pidfile_timeout=30):
    """
    Terminates Celery Beat after `delay_seconds` of beat-runtime.

    Waits up to `pidfile_timeout` seconds for the pidfile to appear before
    starting the runtime timer, so slow process startup doesn't race the
    killer into a FileNotFoundError that would leak a running beat.
    """
    logger.info("Starting Celery Beat killer...")
    deadline = time.monotonic() + pidfile_timeout
    while not os.path.exists(beat_pid_file):
        if time.monotonic() > deadline:
            raise RuntimeError("Celery Beat pidfile %s never appeared" % beat_pid_file)
        time.sleep(0.05)
    time.sleep(delay_seconds)
    pid = int(open(beat_pid_file, "r").read())
    logger.info("Terminating Celery Beat...")
    os.kill(pid, signal.SIGTERM)


def run_beat(celery_app, runtime_seconds=1, loglevel="warning", quiet=True):
    """
    Run Celery Beat that immediately starts tasks.
    The Celery Beat instance is automatically terminated after `runtime_seconds`.
    """
    logger.info("Starting Celery Beat...")
    pid_file = os.path.join(tempfile.mkdtemp(), f"celery-beat-{os.getpid()}.pid")

    t = threading.Thread(
        target=kill_beat,
        args=(pid_file,),
        kwargs={"delay_seconds": runtime_seconds},
    )
    t.start()

    beat_instance = celery_app.Beat(
        loglevel=loglevel,
        quiet=quiet,
        pidfile=pid_file,
    )
    beat_instance.run()
