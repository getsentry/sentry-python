import os
import signal
import sys
import tempfile
import threading
import time
import traceback

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
    starting the runtime timer. Without this, slow process startup (e.g. in
    a Python 3.7 docker container in CI) caused the killer to race the
    pidfile, die with FileNotFoundError, and leave beat running forever.

    Any unexpected exception is dumped to stderr so a future hang surfaces
    a traceback in the CI log instead of silently leaking a dead thread.
    """
    try:
        logger.info("Starting Celery Beat killer...")
        deadline = time.monotonic() + pidfile_timeout
        while not os.path.exists(beat_pid_file):
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Celery Beat pidfile %s never appeared" % beat_pid_file
                )
            time.sleep(0.05)
        time.sleep(delay_seconds)
        pid = int(open(beat_pid_file, "r").read())
        logger.info("Terminating Celery Beat...")
        os.kill(pid, signal.SIGTERM)
    except BaseException:
        sys.stderr.write(
            "kill_beat thread crashed; beat will hang. pidfile=%s\n" % beat_pid_file
        )
        traceback.print_exc()
        sys.stderr.flush()
        raise


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
