import threading
import time

from sentry_sdk.profiler import SleepScheduler


def get_scheduler_threads(scheduler):
    return [
        thread
        for thread in threading.enumerate()
        if thread.name == scheduler.name
    ]

def test_sleep_scheduler_single_background_thread():
    def sampler():
        pass

    scheduler = SleepScheduler(sampler=sampler, frequency=100)

    assert scheduler.start_profiling()

    # the scheduler thread does not immediately exit
    # but it should exit after the next time it samples
    assert scheduler.stop_profiling()

    assert scheduler.start_profiling()

    # because the scheduler thread does not immediately exit
    # after stop_profiling is called, we have to wait a little
    # otherwise, we'll see an extra scheduler thread in the
    # following assertion
    time.sleep(0.1)

    # there should be 1 scheduler thread now because the first
    # one should be stopped and a new one started
    assert len(get_scheduler_threads(scheduler)) == 1

    assert scheduler.stop_profiling()

    # because the scheduler thread does not immediately exit
    # after stop_profiling is called, we have to wait a little
    # otherwise, we'll see an extra scheduler thread in the
    # following assertion
    time.sleep(0.1)

    # there should be 0 scheduler threads now because they stopped
    assert len(get_scheduler_threads(scheduler)) == 0
