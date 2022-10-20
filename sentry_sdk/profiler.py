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
import sys
import threading
import time
import uuid
from collections import deque, namedtuple
from contextlib import contextmanager

import sentry_sdk
from sentry_sdk._compat import PY33
from sentry_sdk._queue import Queue
from sentry_sdk._types import MYPY
from sentry_sdk.utils import nanosecond_time

RawFrameData = namedtuple("RawFrameData", ["function", "abs_path", "lineno"])

if MYPY:
    from types import FrameType
    from typing import Any
    from typing import Callable
    from typing import Deque
    from typing import Dict
    from typing import Generator
    from typing import List
    from typing import Optional
    from typing import Sequence
    from typing import Tuple
    from typing_extensions import TypedDict
    import sentry_sdk.tracing

    RawSampleData = Tuple[int, Sequence[Tuple[int, Sequence[RawFrameData]]]]

    ProcessedStack = Tuple[int, ...]

    ProcessedSample = TypedDict(
        "ProcessedSample",
        {
            "elapsed_since_start_ns": str,
            "thread_id": str,
            "stack_id": int,
        },
    )

    ProcessedFrame = TypedDict(
        "ProcessedFrame",
        {
            "function": str,
            "filename": str,
            "lineno": int,
        },
    )

    ProcessedThreadMetadata = TypedDict(
        "ProcessedThreadMetadata",
        {"name": str},
    )

    ProcessedProfile = TypedDict(
        "ProcessedProfile",
        {
            "frames": List[ProcessedFrame],
            "stacks": List[ProcessedStack],
            "samples": List[ProcessedSample],
            "thread_metadata": Dict[str, ProcessedThreadMetadata],
        },
    )


_sample_buffer = None  # type: Optional[SampleBuffer]
_scheduler = None  # type: Optional[Scheduler]


def setup_profiler(options):
    # type: (Dict[str, Any]) -> None

    """
    `buffer_secs` determines the max time a sample will be buffered for
    `frequency` determines the number of samples to take per second (Hz)
    """
    buffer_secs = 30
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
    _sample_buffer = SampleBuffer(capacity=buffer_secs * frequency)

    profiler_mode = options["_experiments"].get("profiler_mode", SigprofScheduler.mode)
    if profiler_mode == SigprofScheduler.mode:
        _scheduler = SigprofScheduler(sample_buffer=_sample_buffer, frequency=frequency)
    elif profiler_mode == SigalrmScheduler.mode:
        _scheduler = SigalrmScheduler(sample_buffer=_sample_buffer, frequency=frequency)
    elif profiler_mode == SleepScheduler.mode:
        _scheduler = SleepScheduler(sample_buffer=_sample_buffer, frequency=frequency)
    elif profiler_mode == EventScheduler.mode:
        _scheduler = EventScheduler(sample_buffer=_sample_buffer, frequency=frequency)
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


# We want to impose a stack depth limit so that samples aren't too large.
MAX_STACK_DEPTH = 128


def extract_stack(frame, max_stack_depth=MAX_STACK_DEPTH):
    # type: (Optional[FrameType], int) -> Sequence[RawFrameData]
    """
    Extracts the stack starting the specified frame. The extracted stack
    assumes the specified frame is the top of the stack, and works back
    to the bottom of the stack.

    In the event that the stack is more than `MAX_STACK_DEPTH` frames deep,
    only the first `MAX_STACK_DEPTH` frames will be returned.
    """

    stack = deque(maxlen=max_stack_depth)  # type: Deque[FrameType]

    while frame is not None:
        stack.append(frame)
        frame = frame.f_back

    return [
        RawFrameData(
            function=get_frame_name(frame),
            abs_path=frame.f_code.co_filename,
            lineno=frame.f_lineno,
        )
        for frame in stack
    ]


def get_frame_name(frame):
    # type: (FrameType) -> str

    # in 3.11+, there is a frame.f_code.co_qualname that
    # we should consider using instead where possible

    f_code = frame.f_code
    # co_name only contains the frame name.  If the frame was a method,
    # the class name will NOT be included.
    name = f_code.co_name

    # if it was a method, we can get the class name by inspecting
    # the f_locals for the `self` argument
    try:
        if (
            # the co_varnames start with the frame's positional arguments
            # and we expect the first to be `self` if its an instance method
            f_code.co_varnames
            and f_code.co_varnames[0] == "self"
            and "self" in frame.f_locals
        ):
            return "{}.{}".format(frame.f_locals["self"].__class__.__name__, name)
    except AttributeError:
        pass

    # if it was a class method, (decorated with `@classmethod`)
    # we can get the class name by inspecting the f_locals for the `cls` argument
    try:
        if (
            # the co_varnames start with the frame's positional arguments
            # and we expect the first to be `cls` if its a class method
            f_code.co_varnames
            and f_code.co_varnames[0] == "cls"
            and "cls" in frame.f_locals
        ):
            return "{}.{}".format(frame.f_locals["cls"].__name__, name)
    except AttributeError:
        pass

    # nothing we can do if it is a staticmethod (decorated with @staticmethod)

    # we've done all we can, time to give up and return what we have
    return name


class Profile(object):
    def __init__(
        self,
        scheduler,  # type: Scheduler
        transaction,  # type: sentry_sdk.tracing.Transaction
        hub=None,  # type: Optional[sentry_sdk.Hub]
    ):
        # type: (...) -> None
        self.scheduler = scheduler
        self.transaction = transaction
        self.hub = hub
        self._start_ns = None  # type: Optional[int]
        self._stop_ns = None  # type: Optional[int]

        transaction._profile = self

    def __enter__(self):
        # type: () -> None
        self._start_ns = nanosecond_time()
        self.scheduler.start_profiling()

    def __exit__(self, ty, value, tb):
        # type: (Optional[Any], Optional[Any], Optional[Any]) -> None
        self.scheduler.stop_profiling()
        self._stop_ns = nanosecond_time()

    def to_json(self, event_opt):
        # type: (Any) -> Dict[str, Any]
        assert self._start_ns is not None
        assert self._stop_ns is not None

        return {
            "environment": event_opt.get("environment"),
            "event_id": uuid.uuid4().hex,
            "platform": "python",
            "profile": self.scheduler.sample_buffer.slice_profile(
                self._start_ns, self._stop_ns
            ),
            "release": event_opt.get("release", ""),
            "timestamp": event_opt["timestamp"],
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
                    "id": event_opt["event_id"],
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


class SampleBuffer(object):
    """
    A simple implementation of a ring buffer to buffer the samples taken.

    At some point, the ring buffer will start overwriting old samples.
    This is a trade off we've chosen to ensure the memory usage does not
    grow indefinitely. But by having a sufficiently large buffer, this is
    largely not a problem.
    """

    def __init__(self, capacity):
        # type: (int) -> None

        self.buffer = [None] * capacity  # type: List[Optional[RawSampleData]]
        self.capacity = capacity  # type: int
        self.idx = 0  # type: int

    def write(self, sample):
        # type: (RawSampleData) -> None
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
        # type: (int, int) -> ProcessedProfile
        samples = []  # type: List[ProcessedSample]
        stacks = dict()  # type: Dict[ProcessedStack, int]
        stacks_list = list()  # type: List[ProcessedStack]
        frames = dict()  # type: Dict[RawFrameData, int]
        frames_list = list()  # type: List[ProcessedFrame]

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
                current_stack = []

                for frame in stack:
                    if frame not in frames:
                        frames[frame] = len(frames)
                        frames_list.append(
                            {
                                "function": frame.function,
                                "filename": frame.abs_path,
                                "lineno": frame.lineno,
                            }
                        )
                    current_stack.append(frames[frame])

                current_stack = tuple(current_stack)
                if current_stack not in stacks:
                    stacks[current_stack] = len(stacks)
                    stacks_list.append(current_stack)

                samples.append(
                    {
                        "elapsed_since_start_ns": str(ts - start_ns),
                        "thread_id": str(tid),
                        "stack_id": stacks[current_stack],
                    }
                )

        # This collects the thread metadata at the end of a profile. Doing it
        # this way means that any threads that terminate before the profile ends
        # will not have any metadata associated with it.
        thread_metadata = {
            str(thread.ident): {
                "name": thread.name,
            }
            for thread in threading.enumerate()
        }  # type: Dict[str, ProcessedThreadMetadata]

        return {
            "stacks": stacks_list,
            "frames": frames_list,
            "samples": samples,
            "thread_metadata": thread_metadata,
        }

    def make_sampler(self):
        # type: () -> Callable[..., None]

        def _sample_stack(*args, **kwargs):
            # type: (*Any, **Any) -> None
            """
            Take a sample of the stack on all the threads in the process.
            This should be called at a regular interval to collect samples.
            """

            self.write(
                (
                    nanosecond_time(),
                    [
                        (tid, extract_stack(frame))
                        for tid, frame in sys._current_frames().items()
                    ],
                )
            )

        return _sample_stack


class Scheduler(object):
    mode = "unknown"

    def __init__(self, sample_buffer, frequency):
        # type: (SampleBuffer, int) -> None
        self.sample_buffer = sample_buffer
        self.sampler = sample_buffer.make_sampler()
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


class ThreadScheduler(Scheduler):
    """
    This abstract scheduler is based on running a daemon thread that will call
    the sampler at a regular interval.
    """

    mode = "thread"
    name = None  # type: Optional[str]

    def __init__(self, sample_buffer, frequency):
        # type: (SampleBuffer, int) -> None
        super(ThreadScheduler, self).__init__(
            sample_buffer=sample_buffer, frequency=frequency
        )
        self.stop_events = Queue()

    def setup(self):
        # type: () -> None
        pass

    def teardown(self):
        # type: () -> None
        pass

    def start_profiling(self):
        # type: () -> bool
        if super(ThreadScheduler, self).start_profiling():
            # make sure to clear the event as we reuse the same event
            # over the lifetime of the scheduler
            event = threading.Event()
            self.stop_events.put_nowait(event)
            run = self.make_run(event)

            # make sure the thread is a daemon here otherwise this
            # can keep the application running after other threads
            # have exited
            thread = threading.Thread(name=self.name, target=run, daemon=True)
            thread.start()
            return True
        return False

    def stop_profiling(self):
        # type: () -> bool
        if super(ThreadScheduler, self).stop_profiling():
            # make sure the set the event here so that the thread
            # can check to see if it should keep running
            event = self.stop_events.get_nowait()
            event.set()
            return True
        return False

    def make_run(self, event):
        # type: (threading.Event) -> Callable[..., None]
        raise NotImplementedError


class SleepScheduler(ThreadScheduler):
    """
    This scheduler uses time.sleep to wait the required interval before calling
    the sampling function.
    """

    mode = "sleep"
    name = "sentry.profiler.SleepScheduler"

    def make_run(self, event):
        # type: (threading.Event) -> Callable[..., None]

        def run():
            # type: () -> None
            self.sampler()

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

                if event.is_set():
                    break

                self.sampler()

        return run


class EventScheduler(ThreadScheduler):
    """
    This scheduler uses threading.Event to wait the required interval before
    calling the sampling function.
    """

    mode = "event"
    name = "sentry.profiler.EventScheduler"

    def make_run(self, event):
        # type: (threading.Event) -> Callable[..., None]

        def run():
            # type: () -> None
            self.sampler()

            while True:
                event.wait(timeout=self._interval)

                if event.is_set():
                    break

                self.sampler()

        return run


class SignalScheduler(Scheduler):
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
            signal.signal(self.signal_num, self.sampler)
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
        if super(SignalScheduler, self).start_profiling():
            signal.setitimer(self.signal_timer, self._interval, self._interval)
            return True
        return False

    def stop_profiling(self):
        # type: () -> bool
        if super(SignalScheduler, self).stop_profiling():
            signal.setitimer(self.signal_timer, 0)
            return True
        return False


class SigprofScheduler(SignalScheduler):
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


class SigalrmScheduler(SignalScheduler):
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
        assert _scheduler is not None
        with Profile(_scheduler, transaction, hub=hub):
            yield
    else:
        yield
