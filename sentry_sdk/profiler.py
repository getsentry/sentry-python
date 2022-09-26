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
import random
import signal
import threading
import time
import sys
import uuid

from collections import deque
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk._compat import PY33

from sentry_sdk._types import MYPY
from sentry_sdk.utils import nanosecond_time

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


_sample_buffer = None  # type: Optional[_SampleBuffer]
_scheduler = None  # type: Optional[_Scheduler]


def setup_profiler(options):
    # type: (Dict[str, Any]) -> None

    """
    `buffer_secs` determines the max time a sample will be buffered for
    `frequency` determines the number of samples to take per second (Hz)
    """
    buffer_secs = 60
    frequency = 101

    if not PY33:
        from sentry_sdk.utils import logger

        logger.warn("profiling is only supported on Python >= 3.3")
        return

    global _sample_buffer
    global _scheduler

    assert _sample_buffer is None and _scheduler is None

    # To buffer samples for `buffer_secs` at `frequency` Hz, we need
    # a capcity of `buffer_secs * frequency`.
    _sample_buffer = _SampleBuffer(capacity=buffer_secs * frequency)

    profiler_mode = options["_experiments"].get("profiler_mode", _SigprofScheduler.mode)
    if profiler_mode == _SigprofScheduler.mode:
        _scheduler = _SigprofScheduler(frequency=frequency)
    elif profiler_mode == _SigalrmScheduler.mode:
        _scheduler = _SigalrmScheduler(frequency=frequency)
    elif profiler_mode == _SleepScheduler.mode:
        _scheduler = _SleepScheduler(frequency=frequency)
    elif profiler_mode == _EventScheduler.mode:
        _scheduler = _EventScheduler(frequency=frequency)
    else:
        raise ValueError("Unknown profiler mode: {}".format(profiler_mode))
    _scheduler.setup()

    atexit.register(teardown_profiler)


def teardown_profiler():
    # type: () -> None

    global _sample_buffer
    global _scheduler

    if _scheduler is not None:
        _scheduler.teardown()

    _sample_buffer = None
    _scheduler = None


def _sample_stack(*args, **kwargs):
    # type: (*Any, **Any) -> None
    """
    Take a sample of the stack on all the threads in the process.
    This should be called at a regular interval to collect samples.
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
            "environment": None,  # Gets added in client.py
            "event_id": uuid.uuid4().hex,
            "platform": "python",
            "profile": _sample_buffer.slice_profile(self._start_ns, self._stop_ns),
            "release": None,  # Gets added in client.py
            "timestamp": None,  # Gets added in client.py
            "version": "1",
            "device": {
                "architecture": platform.machine(),
            },
            "os": {
                "name": platform.system(),
                "version": platform.release(),
            },
            "runtime": {
                "name": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "transactions": [
                {
                    "id": None,  # Gets added in client.py
                    "name": self.transaction.name,
                    # we start the transaction before the profile and this is
                    # the transaction start time relative to the profile, so we
                    # hardcode it to 0 until we can start the profile before
                    "relative_start_ns": "0",
                    # use the duration of the profile instead of the transaction
                    # because we end the transaction after the profile
                    "relative_end_ns": str(self._stop_ns - self._start_ns),
                    "trace_id": self.transaction.trace_id,
                    "active_thread_id": str(self.transaction._active_thread_id),
                }
            ],
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
        # type: (int, int) -> Dict[str, Any]
        samples = []  # type: List[Any]
        stacks = dict()  # type: Dict[Any, int]
        stacks_list = list()  # type: List[Any]
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
                    "elapsed_since_start_ns": str(ts - start_ns),
                    "thread_id": str(tid),
                }
                current_stack = []

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
                    current_stack.append(frames[frame])

                current_stack = tuple(current_stack)
                if current_stack not in stacks:
                    stacks[current_stack] = len(stacks)
                    stacks_list.append(current_stack)

                sample["stack_id"] = stacks[current_stack]
                samples.append(sample)

        return {"stacks": stacks_list, "frames": frames_list, "samples": samples}


class _Scheduler(object):
    mode = "unknown"

    def __init__(self, frequency):
        # type: (int) -> None
        self._lock = threading.Lock()
        self._count = 0
        self._interval = 1.0 / frequency

    def setup(self):
        # type: () -> None
        raise NotImplementedError

    def teardown(self):
        # type: () -> None
        raise NotImplementedError

    def start_profiling(self):
        # type: () -> bool
        with self._lock:
            self._count += 1
            return self._count == 1

    def stop_profiling(self):
        # type: () -> bool
        with self._lock:
            self._count -= 1
            return self._count == 0


class _ThreadScheduler(_Scheduler):
    """
    This abstract scheduler is based on running a daemon thread that will call
    the sampler at a regular interval.
    """

    mode = "thread"

    def __init__(self, frequency):
        # type: (int) -> None
        super(_ThreadScheduler, self).__init__(frequency)
        self.event = threading.Event()

    def setup(self):
        # type: () -> None
        pass

    def teardown(self):
        # type: () -> None
        pass

    def start_profiling(self):
        # type: () -> bool
        if super(_ThreadScheduler, self).start_profiling():
            # make sure to clear the event as we reuse the same event
            # over the lifetime of the scheduler
            self.event.clear()

            # make sure the thread is a daemon here otherwise this
            # can keep the application running after other threads
            # have exited
            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            return True
        return False

    def stop_profiling(self):
        # type: () -> bool
        if super(_ThreadScheduler, self).stop_profiling():
            # make sure the set the event here so that the thread
            # can check to see if it should keep running
            self.event.set()
            return True
        return False

    def run(self):
        # type: () -> None
        raise NotImplementedError


class _SleepScheduler(_ThreadScheduler):
    """
    This scheduler uses time.sleep to wait the required interval before calling
    the sampling function.
    """

    mode = "sleep"

    def run(self):
        # type: () -> None
        last = time.perf_counter()

        while True:
            # some time may have elapsed since the last time
            # we sampled, so we need to account for that and
            # not sleep for too long
            now = time.perf_counter()
            elapsed = max(now - last, 0)

            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)

            last = time.perf_counter()

            if self.event.is_set():
                break

            _sample_stack()


class _EventScheduler(_ThreadScheduler):
    """
    This scheduler uses threading.Event to wait the required interval before
    calling the sampling function.
    """

    mode = "event"

    def run(self):
        # type: () -> None
        while True:
            self.event.wait(timeout=self._interval)

            if self.event.is_set():
                break

            _sample_stack()


class _SignalScheduler(_Scheduler):
    """
    This abstract scheduler is based on UNIX signals. It sets up a
    signal handler for the specified signal, and the matching itimer in order
    for the signal handler to fire at a regular interval.

    See https://www.gnu.org/software/libc/manual/html_node/Alarm-Signals.html
    """

    mode = "signal"

    @property
    def signal_num(self):
        # type: () -> signal.Signals
        raise NotImplementedError

    @property
    def signal_timer(self):
        # type: () -> int
        raise NotImplementedError

    def setup(self):
        # type: () -> None
        """
        This method sets up the application so that it can be profiled.
        It MUST be called from the main thread. This is a limitation of
        python's signal library where it only allows the main thread to
        set a signal handler.
        """

        # This setups a process wide signal handler that will be called
        # at an interval to record samples.
        try:
            signal.signal(self.signal_num, _sample_stack)
        except ValueError:
            raise ValueError(
                "Signal based profiling can only be enabled from the main thread."
            )

        # Ensures that system calls interrupted by signals are restarted
        # automatically. Otherwise, we may see some strage behaviours
        # such as IOErrors caused by the system call being interrupted.
        signal.siginterrupt(self.signal_num, False)

    def teardown(self):
        # type: () -> None

        # setting the timer with 0 will stop will clear the timer
        signal.setitimer(self.signal_timer, 0)

        # put back the default signal handler
        signal.signal(self.signal_num, signal.SIG_DFL)

    def start_profiling(self):
        # type: () -> bool
        if super(_SignalScheduler, self).start_profiling():
            signal.setitimer(self.signal_timer, self._interval, self._interval)
            return True
        return False

    def stop_profiling(self):
        # type: () -> bool
        if super(_SignalScheduler, self).stop_profiling():
            signal.setitimer(self.signal_timer, 0)
            return True
        return False


class _SigprofScheduler(_SignalScheduler):
    """
    This scheduler uses SIGPROF to regularly call a signal handler where the
    samples will be taken.

    This is not based on wall time, and you may see some variances
    in the frequency at which this handler is called.

    This has some limitations:
    - Only the main thread counts towards the time elapsed. This means that if
      the main thread is blocking on a sleep() or select() system call, then
      this clock will not count down. Some examples of this in practice are
        - When using uwsgi with multiple threads in a worker, the non main
          threads will only be profiled if the main thread is actively running
          at the same time.
        - When using gunicorn with threads, the main thread does not handle the
          requests directly, so the clock counts down slower than expected since
          its mostly idling while waiting for requests.
    """

    mode = "sigprof"

    @property
    def signal_num(self):
        # type: () -> signal.Signals
        return signal.SIGPROF

    @property
    def signal_timer(self):
        # type: () -> int
        return signal.ITIMER_PROF


class _SigalrmScheduler(_SignalScheduler):
    """
    This scheduler uses SIGALRM to regularly call a signal handler where the
    samples will be taken.

    This is based on real time, so it *should* be called close to the expected
    frequency.
    """

    mode = "sigalrm"

    @property
    def signal_num(self):
        # type: () -> signal.Signals
        return signal.SIGALRM

    @property
    def signal_timer(self):
        # type: () -> int
        return signal.ITIMER_REAL


def _should_profile(transaction, hub):
    # type: (sentry_sdk.tracing.Transaction, Optional[sentry_sdk.Hub]) -> bool

    # The corresponding transaction was not sampled,
    # so don't generate a profile for it.
    if not transaction.sampled:
        return False

    # The profiler hasn't been properly initialized.
    if _sample_buffer is None or _scheduler is None:
        return False

    hub = hub or sentry_sdk.Hub.current
    client = hub.client

    # The client is None, so we can't get the sample rate.
    if client is None:
        return False

    options = client.options
    profiles_sample_rate = options["_experiments"].get("profiles_sample_rate")

    # The profiles_sample_rate option was not set, so profiling
    # was never enabled.
    if profiles_sample_rate is None:
        return False

    return random.random() < float(profiles_sample_rate)


@contextmanager
def start_profiling(transaction, hub=None):
    # type: (sentry_sdk.tracing.Transaction, Optional[sentry_sdk.Hub]) -> Generator[None, None, None]

    # if profiling was not enabled, this should be a noop
    if _should_profile(transaction, hub):
        with Profile(transaction, hub=hub):
            yield
    else:
        yield
