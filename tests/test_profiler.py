import platform
import sys
import threading

import pytest

from sentry_sdk.profiler import setup_profiler


minimum_python_33 = pytest.mark.skipif(
    sys.version_info < (3, 3), reason="Profiling is only supported in Python >= 3.3"
)

unix_only = pytest.mark.skipif(
    platform.system().lower() not in {"linux", "darwin"}, reason="UNIX only"
)


@minimum_python_33
def test_profiler_invalid_mode(teardown_profiling):
    with pytest.raises(ValueError):
        setup_profiler({"_experiments": {"profiler_mode": "magic"}})
    # make sure to clean up at the end of the test


@unix_only
@minimum_python_33
@pytest.mark.parametrize("mode", ["sigprof", "sigalrm"])
def test_profiler_signal_mode_none_main_thread(mode, teardown_profiling):
    """
    signal based profiling must be initialized from the main thread because
    of how the signal library in python works
    """

    class ProfilerThread(threading.Thread):
        def run(self):
            self.exc = None
            try:
                setup_profiler({"_experiments": {"profiler_mode": mode}})
            except Exception as e:
                # store the exception so it can be raised in the caller
                self.exc = e

        def join(self, timeout=None):
            ret = super(ProfilerThread, self).join(timeout=timeout)
            if self.exc:
                raise self.exc
            return ret

    with pytest.raises(ValueError):
        thread = ProfilerThread()
        thread.start()
        thread.join()

    # make sure to clean up at the end of the test


@pytest.mark.parametrize("mode", ["sleep", "event", "sigprof", "sigalrm"])
def test_profiler_valid_mode(mode, teardown_profiling):
    # should not raise any exceptions
    setup_profiler({"_experiments": {"profiler_mode": mode}})
