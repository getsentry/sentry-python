import os
import sys
import threading
import time
import uuid

from sentry_sdk._compat import PY33, datetime_utcnow
from sentry_sdk._lru_cache import LRUCache
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.profiler import DEFAULT_SAMPLING_FREQUENCY, extract_stack
from sentry_sdk.utils import (
    capture_internal_exception,
    is_gevent,
    logger,
    now,
    set_in_app_in_frames,
)


if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing_extensions import TypedDict
    from sentry_sdk._types import ContinuousProfilerMode
    from sentry_sdk.profiler import (
        ExtractedSample,
        FrameId,
        StackId,
        ThreadId,
        ProcessedFrame,
        ProcessedStack,
    )

    ProcessedSample = TypedDict(
        "ProcessedSample",
        {
            "timestamp": float,
            "thread_id": ThreadId,
            "stack_id": int,
        },
    )


try:
    from gevent.monkey import get_original  # type: ignore
    from gevent.threadpool import ThreadPool  # type: ignore

    thread_sleep = get_original("time", "sleep")
except ImportError:
    thread_sleep = time.sleep
    ThreadPool = None


_scheduler = None  # type: Optional[ContinuousScheduler]


def setup_continuous_profiler(options):
    # type: (Dict[str, Any]) -> bool
    global _scheduler

    if _scheduler is not None:
        logger.debug("[Profiling] Continuous Profiler is already setup")
        return False

    if not PY33:
        logger.warn("[Profiling] Continuous Profiler requires Python >= 3.3")
        return False

    if is_gevent():
        # If gevent has patched the threading modules then we cannot rely on
        # them to spawn a native thread for sampling.
        # Instead we default to the GeventContinuousScheduler which is capable of
        # spawning native threads within gevent.
        default_profiler_mode = GeventContinuousScheduler.mode
    else:
        default_profiler_mode = ThreadContinuousScheduler.mode

    experiments = options.get("_experiments", {})

    profiler_mode = experiments.get("continuous_profiling_mode") or default_profiler_mode

    frequency = DEFAULT_SAMPLING_FREQUENCY

    if profiler_mode == ThreadContinuousScheduler.mode:
        _scheduler = ThreadContinuousScheduler(frequency, options)
    elif profiler_mode == GeventContinuousScheduler.mode:
        _scheduler = GeventContinuousScheduler(frequency, options)
    else:
        raise ValueError("Unknown continuous profiler mode: {}".format(profiler_mode))

    logger.debug(
        "[Profiling] Setting up continuous profiler in {mode} mode".format(mode=_scheduler.mode)
    )

    return True


def teardown_continuous_profiler():
    # type: () -> None

    global _scheduler

    if _scheduler is not None:
        _scheduler.teardown()

    _scheduler = None


def try_ensure_continuous_profiler_running():
    # type: () -> None
    if _scheduler is None:
        return

    _scheduler.ensure_running()


def has_continous_profiling_enabled(options):
    # type: (Dict[str, Any]) -> bool
    experiments = options.get("_experiments", {})
    if experiments.get("enable_continuous_profiling"):
        return True
    return False


class ContinuousScheduler(object):
    mode = "unknown"  # type: ContinuousProfilerMode

    def __init__(self, frequency, options):
        # type: (int, Dict[str, Any]) -> None
        self.interval = 1.0 / frequency
        self.options = options
        self.sampler = self.make_sampler()
        self.buffer = None  # type: Optional[ProfileBuffer]

    def ensure_running(self):
        # type: () -> None
        raise NotImplementedError

    def teardown(self):
        # type: () -> None
        raise NotImplementedError

    def reset_buffer(self):
        # type: () -> None
        self.buffer = ProfileBuffer(self.options)

    def make_sampler(self):
        # type: () -> Callable[..., None]
        cwd = os.getcwd()

        cache = LRUCache(max_size=256)

        def _sample_stack(*args, **kwargs):
            # type: (*Any, **Any) -> None
            """
            Take a sample of the stack on all the threads in the process.
            This should be called at a regular interval to collect samples.
            """

            ts = now()

            try:
                sample = [
                    (str(tid), extract_stack(frame, cache, cwd))
                    for tid, frame in sys._current_frames().items()
                ]
            except AttributeError:
                # For some reason, the frame we get doesn't have certain attributes.
                # When this happens, we abandon the current sample as it's bad.
                capture_internal_exception(sys.exc_info())
                return

            if self.buffer is not None:
                self.buffer.write(ts, sample)

        return _sample_stack


class ThreadContinuousScheduler(ContinuousScheduler):
    """
    This scheduler is based on running a daemon thread that will call
    the sampler at a regular interval.
    """

    mode = "thread"  # type: ContinuousProfilerMode
    name = "sentry.profiler.ThreadContinuousScheduler"

    def __init__(self, frequency, options):
        # type: (int, Dict[str, Any]) -> None
        super(ThreadContinuousScheduler, self).__init__(frequency, options)

        self.thread = None  # type: Optional[threading.Thread]
        self.running = False
        self.pid = None  # type: Optional[int]
        self.lock = threading.Lock()

    def ensure_running(self):
        # type: () -> None
        pid = os.getpid()

        # is running on the right process
        if self.running and self.pid == pid:
            return

        with self.lock:
            # another thread may have tried to acquire the lock
            # at the same time so it may start another thread
            # make sure to check again before proceeding
            if self.running and self.pid == pid:
                return

            self.pid = pid
            self.running = True

            # if the profiler thread is changing,
            # we should create a new buffer along with it
            self.reset_buffer()

            # make sure the thread is a daemon here otherwise this
            # can keep the application running after other threads
            # have exited
            self.thread = threading.Thread(name=self.name, target=self.run, daemon=True)

            try:
                self.thread.start()
            except RuntimeError:
                # Unfortunately at this point the interpreter is in a state that no
                # longer allows us to spawn a thread and we have to bail.
                self.running = False
                self.thread = None

    def run(self):
        # type: () -> None
        last = time.perf_counter()

        while self.running:
            self.sampler()

            # some time may have elapsed since the last time
            # we sampled, so we need to account for that and
            # not sleep for too long
            elapsed = time.perf_counter() - last
            if elapsed < self.interval:
                thread_sleep(self.interval - elapsed)

            # after sleeping, make sure to take the current
            # timestamp so we can use it next iteration
            last = time.perf_counter()

    def teardown(self):
        # type: () -> None
        if self.running:
            self.running = False
            if self.thread is not None:
                self.thread.join()


class GeventContinuousScheduler(ContinuousScheduler):
    """
    This scheduler is based on the thread scheduler but adapted to work with
    gevent. When using gevent, it may monkey patch the threading modules
    (`threading` and `_thread`). This results in the use of greenlets instead
    of native threads.

    This is an issue because the sampler CANNOT run in a greenlet because
    1. Other greenlets doing sync work will prevent the sampler from running
    2. The greenlet runs in the same thread as other greenlets so when taking
       a sample, other greenlets will have been evicted from the thread. This
       results in a sample containing only the sampler's code.
    """

    mode = "gevent"  # type: ContinuousProfilerMode

    def __init__(self, frequency, options):
        # type: (int, Dict[str, Any]) -> None

        if ThreadPool is None:
            raise ValueError("Profiler mode: {} is not available".format(self.mode))

        super(GeventContinuousScheduler, self).__init__(frequency, options)

        self.thread = None  # type: Optional[ThreadPool]
        self.running = False
        self.pid = None  # type: Optional[int]
        self.lock = threading.Lock()

    def ensure_running(self):
        # type: () -> None
        pid = os.getpid()

        # is running on the right process
        if self.running and self.pid == pid:
            return

        with self.lock:
            # another thread may have tried to acquire the lock
            # at the same time so it may start another thread
            # make sure to check again before proceeding
            if self.running and self.pid == pid:
                return

            self.pid = pid
            self.running = True

            # if the profiler thread is changing,
            # we should create a new buffer along with it
            self.reset_buffer()

            self.thread = ThreadPool(1)
            try:
                self.thread.spawn(self.run)
            except RuntimeError:
                # Unfortunately at this point the interpreter is in a state that no
                # longer allows us to spawn a thread and we have to bail.
                self.running = False
                self.thread = None
                return

    def run(self):
        # type: () -> None
        last = time.perf_counter()

        while self.running:
            self.sampler()

            # some time may have elapsed since the last time
            # we sampled, so we need to account for that and
            # not sleep for too long
            elapsed = time.perf_counter() - last
            if elapsed < self.interval:
                thread_sleep(self.interval - elapsed)

            # after sleeping, make sure to take the current
            # timestamp so we can use it next iteration
            last = time.perf_counter()

    def teardown(self):
        # type: () -> None
        if self.running:
            self.running = False
            if self.thread is not None:
                self.thread.join()


class ProfileBuffer(object):
    def __init__(self, options, buffer_size=1):
        # type: (Dict[str, Any], int) -> None
        self.profiler_id = uuid.uuid4().hex
        self.options = options
        self.buffer_size = buffer_size
        self.chunk = ProfileChunk(self.buffer_size)

    def write(self, ts, sample):
        # type: (float, ExtractedSample) -> None
        if self.chunk.is_full(ts):
            self.flush()
            self.chunk = ProfileChunk(self.buffer_size)

        self.chunk.write(ts, sample)

    def flush(self):
        # type: () -> None
        chunk = self.chunk
        chunk.to_json(self.profiler_id, self.options)
        # TODO: flush chunk
        return


class ProfileChunk(object):
    def __init__(self, buffer_size):
        # type: (int) -> None
        self.chunk_id = uuid.uuid4().hex
        self.buffer_size = buffer_size
        self.monotonic_time = now()
        self.start_timestamp = datetime_utcnow().timestamp() - self.monotonic_time

        self.indexed_frames = {}  # type: Dict[FrameId, int]
        self.indexed_stacks = {}  # type: Dict[StackId, int]
        self.frames = []  # type: List[ProcessedFrame]
        self.stacks = []  # type: List[ProcessedStack]
        self.samples = []  # type: List[ProcessedSample]

    def is_full(self, ts):
        # type: (float) -> bool
        return ts - self.monotonic_time >= self.buffer_size

    def write(self, relative_ts, sample):
        # type: (float, ExtractedSample) -> None
        for tid, (stack_id, frame_ids, frames) in sample:
            try:
                # Check if the stack is indexed first, this lets us skip
                # indexing frames if it's not necessary
                if stack_id not in self.indexed_stacks:
                    for i, frame_id in enumerate(frame_ids):
                        if frame_id not in self.indexed_frames:
                            self.indexed_frames[frame_id] = len(self.indexed_frames)
                            self.frames.append(frames[i])

                    self.indexed_stacks[stack_id] = len(self.indexed_stacks)
                    self.stacks.append(
                        [self.indexed_frames[frame_id] for frame_id in frame_ids]
                    )

                self.samples.append(
                    {
                        "timestamp": self.start_timestamp + relative_ts,
                        "thread_id": tid,
                        "stack_id": self.indexed_stacks[stack_id],
                    }
                )
            except AttributeError:
                # For some reason, the frame we get doesn't have certain attributes.
                # When this happens, we abandon the current sample as it's bad.
                capture_internal_exception(sys.exc_info())

    def to_json(self, profiler_id, options):
        # type: (str, Dict[str, Any]) -> Dict[str, Any]
        profile = {
            "frames": self.frames,
            "stacks": self.stacks,
            "samples": self.samples,
            "thread_metadata": {
                str(thread.ident): {
                    "name": str(thread.name),
                }
                for thread in threading.enumerate()
            },
        }

        set_in_app_in_frames(
            profile["frames"],
            options["in_app_exclude"],
            options["in_app_include"],
            options["project_root"],
        )

        return {
            "profiler_id": profiler_id,
            "chunk_id": self.chunk_id,
            "environment": options["environment"],
            "release": options["release"],
            "platform": "python",
            "version": "2",
            "profile": profile,
        }
