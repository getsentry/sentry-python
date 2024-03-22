import time


def _now_seconds_since_epoch():
    # type: () -> float
    # We cannot use `time.perf_counter()` when dealing with the duration
    # of a Celery task, because the start of a Celery task and
    # the end are recorded in different processes.
    # Start happens in the Celery Beat process,
    # the end in a Celery Worker process.
    return time.time()
