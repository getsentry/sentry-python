"""
This file is originally based on code from https://github.com/nylas/nylas-perftools, which is published under the following license:

The MIT License (MIT)

Copyright (c) 2014 Nylas

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

import atexit
import platform
import signal
import threading
import time
import sys
import uuid

from collections import deque
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk._compat import PY2

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any
    from typing import Deque
    from typing import Dict
    from typing import Generator
    from typing import List
    from typing import Optional
    from typing import Sequence
    from typing import Tuple
    import sentry_sdk.tracing

    Frame = Any
    FrameData = Tuple[str, str, int]


if PY2:

    def nanosecond_time():
        # type: () -> int
        return int(time.clock() * 1e9)

else:

    def nanosecond_time():
        # type: () -> int

        # In python3.7+, there is a time.perf_counter_ns()
        # that we may want to switch to for more precision
        return int(time.perf_counter() * 1e9)


_sample_buffer = None  # type: Optional[_SampleBuffer]
_scheduler = None  # type: Optional[_Scheduler]


def _setup_profiler(buffer_secs=60, frequency=101):
    # type: (int, int) -> None

    """
    This method sets up the application so that it can be profiled.
    It MUST be called from the main thread. This is a limitation of
    python's signal library where it only allows the main thread to
    set a signal handler.

    `buffer_secs` determines the max time a sample will be buffered for
    `frequency` determines the number of samples to take per second (Hz)
    """

    global _sample_buffer
    global _scheduler

    assert _sample_buffer is None and _scheduler is None

    # To buffer samples for `buffer_secs` at `frequency` Hz, we need
    # a capcity of `buffer_secs * frequency`.
    _sample_buffer = _SampleBuffer(capacity=buffer_secs * frequency)

    _scheduler = _Scheduler(frequency=frequency)

    # This setups a process wide signal handler that will be called
    # at an interval to record samples.
    signal.signal(signal.SIGPROF, _sample_stack)
    atexit.register(_teardown_profiler)


def _teardown_profiler():
    # type: () -> None

    global _sample_buffer
    global _scheduler

    assert _sample_buffer is not None and _scheduler is not None

    _sample_buffer = None
    _scheduler = None

    # setting the timer with 0 will stop will clear the timer
    signal.setitimer(signal.ITIMER_PROF, 0)

    # put back the default signal handler
    signal.signal(signal.SIGPROF, signal.SIG_DFL)


def _sample_stack(_signal_num, _frame):
    # type: (int, Frame) -> None
    """
    Take a sample of the stack on all the threads in the process.
    This handler is called to handle the signal at a set interval.

    See https://www.gnu.org/software/libc/manual/html_node/Alarm-Signals.html

    This is not based on wall time, and you may see some variances
    in the frequency at which this handler is called.

    Notably, it looks like only threads started using the threading
    module counts towards the time elapsed. It is unclear why that
    is the case right now. However, we are able to get samples from
    threading._DummyThread if this handler is called as a result of
    another thread (e.g. the main thread).
    """

    assert _sample_buffer is not None
    _sample_buffer.write(
        (
            nanosecond_time(),
            [
                (tid, _extract_stack(frame))
                for tid, frame in sys._current_frames().items()
            ],
        )
    )


# We want to impose a stack depth limit so that samples aren't too large.
MAX_STACK_DEPTH = 128


def _extract_stack(frame):
    # type: (Frame) -> Sequence[FrameData]
    """
    Extracts the stack starting the specified frame. The extracted stack
    assumes the specified frame is the top of the stack, and works back
    to the bottom of the stack.

    In the event that the stack is more than `MAX_STACK_DEPTH` frames deep,
    only the first `MAX_STACK_DEPTH` frames will be returned.
    """

    stack = deque(maxlen=MAX_STACK_DEPTH)  # type: Deque[FrameData]

    while frame is not None:
        stack.append(
            (
                # co_name only contains the frame name.
                # If the frame was a class method,
                # the class name will NOT be included.
                frame.f_code.co_name,
                frame.f_code.co_filename,
                frame.f_code.co_firstlineno,
            )
        )
        frame = frame.f_back

    return stack


class Profile(object):
    def __init__(self, transaction, hub=None):
        # type: (sentry_sdk.tracing.Transaction, Optional[sentry_sdk.Hub]) -> None
        self.transaction = transaction
        self.hub = hub
        self._start_ns = None  # type: Optional[int]
        self._stop_ns = None  # type: Optional[int]

    def __enter__(self):
        # type: () -> None
        assert _scheduler is not None
        self._start_ns = nanosecond_time()
        _scheduler.start_profiling()

    def __exit__(self, ty, value, tb):
        # type: (Optional[Any], Optional[Any], Optional[Any]) -> None
        assert _scheduler is not None
        _scheduler.stop_profiling()
        self._stop_ns = nanosecond_time()

        # Now that we've collected all the data, attach it to the
        # transaction so that it can be sent in the same envelope
        self.transaction._profile = self.to_json()

    def to_json(self):
        # type: () -> Dict[str, Any]
        assert _sample_buffer is not None
        assert self._start_ns is not None
        assert self._stop_ns is not None

        return {
            "device_os_name": platform.system(),
            "device_os_version": platform.release(),
            "duration_ns": str(self._stop_ns - self._start_ns),
            "environment": None,  # Gets added in client.py
            "platform": "python",
            "platform_version": platform.python_version(),
            "profile_id": uuid.uuid4().hex,
            "profile": _sample_buffer.slice_profile(self._start_ns, self._stop_ns),
            "trace_id": self.transaction.trace_id,
            "transaction_id": None,  # Gets added in client.py
            "transaction_name": self.transaction.name,
            "version_code": "",  # TODO: Determine appropriate value. Currently set to empty string so profile will not get rejected.
            "version_name": None,  # Gets added in client.py
        }


class _SampleBuffer(object):
    """
    A simple implementation of a ring buffer to buffer the samples taken.

    At some point, the ring buffer will start overwriting old samples.
    This is a trade off we've chosen to ensure the memory usage does not
    grow indefinitely. But by having a sufficiently large buffer, this is
    largely not a problem.
    """

    def __init__(self, capacity):
        # type: (int) -> None

        self.buffer = [None] * capacity
        self.capacity = capacity
        self.idx = 0

    def write(self, sample):
        # type: (Any) -> None
        """
        Writing to the buffer is not thread safe. There is the possibility
        that parallel writes will overwrite one another.

        This should only be a problem if the signal handler itself is
        interrupted by the next signal.
        (i.e. SIGPROF is sent again before the handler finishes).

        For this reason, and to keep it performant, we've chosen not to add
        any synchronization mechanisms here like locks.
        """
        idx = self.idx
        self.buffer[idx] = sample
        self.idx = (idx + 1) % self.capacity

    def slice_profile(self, start_ns, stop_ns):
        # type: (int, int) -> Dict[str, List[Any]]
        samples = []  # type: List[Any]
        frames = dict()  # type: Dict[FrameData, int]
        frames_list = list()  # type: List[Any]

        # TODO: This is doing an naive iteration over the
        # buffer and extracting the appropriate samples.
        #
        # Is it safe to assume that the samples are always in
        # chronological order and binary search the buffer?
        for raw_sample in self.buffer:
            if raw_sample is None:
                continue

            ts = raw_sample[0]
            if start_ns > ts or ts > stop_ns:
                continue

            for tid, stack in raw_sample[1]:
                sample = {
                    "frames": [],
                    "relative_timestamp_ns": ts - start_ns,
                    "thread_id": tid,
                }

                for frame in stack:
                    if frame not in frames:
                        frames[frame] = len(frames)
                        frames_list.append(
                            {
                                "name": frame[0],
                                "file": frame[1],
                                "line": frame[2],
                            }
                        )
                    sample["frames"].append(frames[frame])

                samples.append(sample)

        return {"frames": frames_list, "samples": samples}


class _Scheduler(object):
    def __init__(self, frequency):
        # type: (int) -> None
        self._lock = threading.Lock()
        self._count = 0
        self._interval = 1.0 / frequency

    def start_profiling(self):
        # type: () -> bool
        with self._lock:
            # we only need to start the timer if we're starting the first profile
            should_start_timer = self._count == 0
            self._count += 1

        if should_start_timer:
            signal.setitimer(signal.ITIMER_PROF, self._interval, self._interval)
        return should_start_timer

    def stop_profiling(self):
        # type: () -> bool
        with self._lock:
            # we only need to stop the timer if we're stoping the last profile
            should_stop_timer = self._count == 1
            self._count -= 1

        if should_stop_timer:
            signal.setitimer(signal.ITIMER_PROF, 0)
        return should_stop_timer


def _has_profiling_enabled():
    # type: () -> bool
    return _sample_buffer is not None and _scheduler is not None


@contextmanager
def start_profiling(transaction, hub=None):
    # type: (sentry_sdk.tracing.Transaction, Optional[sentry_sdk.Hub]) -> Generator[None, None, None]

    # if profiling was not enabled, this should be a noop
    if _has_profiling_enabled():
        with Profile(transaction, hub=hub):
            yield
    else:
        yield
