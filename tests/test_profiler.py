import inspect
import platform
import sys
import threading
import time

import pytest

from sentry_sdk.profiler import (
    EventScheduler,
    RawFrameData,
    SampleBuffer,
    SleepScheduler,
    extract_stack,
    get_frame_name,
    setup_profiler,
)


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


@unix_only
@pytest.mark.parametrize("mode", ["sleep", "event", "sigprof", "sigalrm"])
def test_profiler_valid_mode(mode, teardown_profiling):
    # should not raise any exceptions
    setup_profiler({"_experiments": {"profiler_mode": mode}})


def get_frame(depth=1):
    """
    This function is not exactly true to its name. Depending on
    how it is called, the true depth of the stack can be deeper
    than the argument implies.
    """
    if depth <= 0:
        raise ValueError("only positive integers allowed")
    if depth > 1:
        return get_frame(depth=depth - 1)
    return inspect.currentframe()


class GetFrame:
    def instance_method(self):
        return inspect.currentframe()

    def instance_method_wrapped(self):
        def wrapped():
            self
            return inspect.currentframe()

        return wrapped

    @classmethod
    def class_method(cls):
        return inspect.currentframe()

    @classmethod
    def class_method_wrapped(cls):
        def wrapped():
            cls
            return inspect.currentframe()

        return wrapped

    @staticmethod
    def static_method():
        return inspect.currentframe()


@pytest.mark.parametrize(
    ("frame", "frame_name"),
    [
        pytest.param(
            get_frame(),
            "get_frame",
            id="function",
        ),
        pytest.param(
            (lambda: inspect.currentframe())(),
            "<lambda>",
            id="lambda",
        ),
        pytest.param(
            GetFrame().instance_method(),
            "GetFrame.instance_method",
            id="instance_method",
        ),
        pytest.param(
            GetFrame().instance_method_wrapped()(),
            "wrapped",
            id="instance_method_wrapped",
        ),
        pytest.param(
            GetFrame().class_method(),
            "GetFrame.class_method",
            id="class_method",
        ),
        pytest.param(
            GetFrame().class_method_wrapped()(),
            "wrapped",
            id="class_method_wrapped",
        ),
        pytest.param(
            GetFrame().static_method(),
            "GetFrame.static_method",
            id="static_method",
            marks=pytest.mark.skip(reason="unsupported"),
        ),
    ],
)
def test_get_frame_name(frame, frame_name):
    assert get_frame_name(frame) == frame_name


@pytest.mark.parametrize(
    ("depth", "max_stack_depth", "actual_depth"),
    [
        pytest.param(1, 128, 1, id="less than"),
        pytest.param(256, 128, 128, id="greater than"),
        pytest.param(128, 128, 128, id="equals"),
    ],
)
def test_extract_stack_with_max_depth(depth, max_stack_depth, actual_depth):
    # introduce a lambda that we'll be looking for in the stack
    frame = (lambda: get_frame(depth=depth))()

    # plus 1 because we introduced a lambda intentionally that we'll
    # look for in the final stack to make sure its in the right position
    base_stack_depth = len(inspect.stack()) + 1

    # increase the max_depth by the `base_stack_depth` to account
    # for the extra frames pytest will add
    stack = extract_stack(frame, max_stack_depth + base_stack_depth)
    assert len(stack) == base_stack_depth + actual_depth

    for i in range(actual_depth):
        assert stack[i].function == "get_frame", i

    # index 0 contains the inner most frame on the stack, so the lamdba
    # should be at index `actual_depth`
    assert stack[actual_depth].function == "<lambda>", actual_depth


def get_scheduler_threads(scheduler):
    return [thread for thread in threading.enumerate() if thread.name == scheduler.name]


class DummySampleBuffer(SampleBuffer):
    def __init__(self, capacity, sample_data=None):
        super(DummySampleBuffer, self).__init__(capacity)
        self.sample_data = [] if sample_data is None else sample_data

    def make_sampler(self):
        def _sample_stack(*args, **kwargs):
            print("writing", self.sample_data[0])
            self.write(self.sample_data.pop(0))

        return _sample_stack


@minimum_python_33
@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(SleepScheduler, id="sleep scheduler"),
        pytest.param(EventScheduler, id="event scheduler"),
    ],
)
def test_thread_scheduler_takes_first_samples(scheduler_class):
    sample_buffer = DummySampleBuffer(
        capacity=1,
        sample_data=[
            (
                0,
                [
                    (
                        0,
                        (
                            RawFrameData(
                                "/path/to/file.py", "file.py", "name", 1, "file"
                            ),
                        ),
                    )
                ],
            )
        ],
    )
    scheduler = scheduler_class(sample_buffer=sample_buffer, frequency=1000)
    assert scheduler.start_profiling()
    # immediately stopping means by the time the sampling thread will exit
    # before it samples at the end of the first iteration
    assert scheduler.stop_profiling()
    time.sleep(0.002)
    assert len(get_scheduler_threads(scheduler)) == 0

    # there should be exactly 1 sample because we always sample once immediately
    profile = sample_buffer.slice_profile(0, 1)
    assert len(profile["samples"]) == 1


@minimum_python_33
@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(SleepScheduler, id="sleep scheduler"),
        pytest.param(EventScheduler, id="event scheduler"),
    ],
)
def test_thread_scheduler_takes_more_samples(scheduler_class):
    sample_buffer = DummySampleBuffer(
        capacity=10,
        sample_data=[
            (
                i,
                [
                    (
                        0,
                        (
                            RawFrameData(
                                "/path/to/file.py", "file.py", "name", 1, "file"
                            ),
                        ),
                    )
                ],
            )
            for i in range(3)
        ],
    )
    scheduler = scheduler_class(sample_buffer=sample_buffer, frequency=1000)
    assert scheduler.start_profiling()
    # waiting a little before stopping the scheduler means the profiling
    # thread will get a chance to take a few samples before exiting
    time.sleep(0.002)
    assert scheduler.stop_profiling()
    time.sleep(0.002)
    assert len(get_scheduler_threads(scheduler)) == 0

    # there should be more than 1 sample because we always sample once immediately
    # plus any samples take afterwards
    profile = sample_buffer.slice_profile(0, 3)
    assert len(profile["samples"]) > 1


@minimum_python_33
@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(SleepScheduler, id="sleep scheduler"),
        pytest.param(EventScheduler, id="event scheduler"),
    ],
)
def test_thread_scheduler_single_background_thread(scheduler_class):
    sample_buffer = SampleBuffer(1)
    scheduler = scheduler_class(sample_buffer=sample_buffer, frequency=1000)

    assert scheduler.start_profiling()

    # the scheduler thread does not immediately exit
    # but it should exit after the next time it samples
    assert scheduler.stop_profiling()

    assert scheduler.start_profiling()

    # because the scheduler thread does not immediately exit
    # after stop_profiling is called, we have to wait a little
    # otherwise, we'll see an extra scheduler thread in the
    # following assertion
    #
    # one iteration of the scheduler takes 1.0 / frequency seconds
    # so make sure this sleeps for longer than that to avoid flakes
    time.sleep(0.002)

    # there should be 1 scheduler thread now because the first
    # one should be stopped and a new one started
    assert len(get_scheduler_threads(scheduler)) == 1

    assert scheduler.stop_profiling()

    # because the scheduler thread does not immediately exit
    # after stop_profiling is called, we have to wait a little
    # otherwise, we'll see an extra scheduler thread in the
    # following assertion
    #
    # one iteration of the scheduler takes 1.0 / frequency seconds
    # so make sure this sleeps for longer than that to avoid flakes
    time.sleep(0.002)

    # there should be 0 scheduler threads now because they stopped
    assert len(get_scheduler_threads(scheduler)) == 0


current_thread = threading.current_thread()
thread_metadata = {
    str(current_thread.ident): {
        "name": str(current_thread.name),
    },
}


@pytest.mark.parametrize(
    ("capacity", "start_ns", "stop_ns", "samples", "profile"),
    [
        pytest.param(
            10,
            0,
            1,
            [],
            {
                "frames": [],
                "samples": [],
                "stacks": [],
                "thread_metadata": thread_metadata,
            },
            id="empty",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (
                    2,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name", 1, "file"
                                ),
                            ),
                        )
                    ],
                )
            ],
            {
                "frames": [],
                "samples": [],
                "stacks": [],
                "thread_metadata": thread_metadata,
            },
            id="single sample out of range",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name", 1, "file"
                                ),
                            ),
                        )
                    ],
                )
            ],
            {
                "frames": [
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name",
                        "filename": "file.py",
                        "lineno": 1,
                        "module": "file",
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "0",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                ],
                "stacks": [(0,)],
                "thread_metadata": thread_metadata,
            },
            id="single sample in range",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name", 1, "file"
                                ),
                            ),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name", 1, "file"
                                ),
                            ),
                        )
                    ],
                ),
            ],
            {
                "frames": [
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name",
                        "filename": "file.py",
                        "lineno": 1,
                        "module": "file",
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "0",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                    {
                        "elapsed_since_start_ns": "1",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                ],
                "stacks": [(0,)],
                "thread_metadata": thread_metadata,
            },
            id="two identical stacks",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name1", 1, "file"
                                ),
                            ),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name1", 1, "file"
                                ),
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name2", 2, "file"
                                ),
                            ),
                        )
                    ],
                ),
            ],
            {
                "frames": [
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name1",
                        "filename": "file.py",
                        "lineno": 1,
                        "module": "file",
                    },
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name2",
                        "filename": "file.py",
                        "lineno": 2,
                        "module": "file",
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "0",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                    {
                        "elapsed_since_start_ns": "1",
                        "thread_id": "1",
                        "stack_id": 1,
                    },
                ],
                "stacks": [(0,), (0, 1)],
                "thread_metadata": thread_metadata,
            },
            id="two identical frames",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name1", 1, "file"
                                ),
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name2", 2, "file"
                                ),
                            ),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name3", 3, "file"
                                ),
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name4", 4, "file"
                                ),
                            ),
                        )
                    ],
                ),
            ],
            {
                "frames": [
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name1",
                        "filename": "file.py",
                        "lineno": 1,
                        "module": "file",
                    },
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name2",
                        "filename": "file.py",
                        "lineno": 2,
                        "module": "file",
                    },
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name3",
                        "filename": "file.py",
                        "lineno": 3,
                        "module": "file",
                    },
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name4",
                        "filename": "file.py",
                        "lineno": 4,
                        "module": "file",
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "0",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                    {
                        "elapsed_since_start_ns": "1",
                        "thread_id": "1",
                        "stack_id": 1,
                    },
                ],
                "stacks": [(0, 1), (2, 3)],
                "thread_metadata": thread_metadata,
            },
            id="two unique stacks",
        ),
        pytest.param(
            1,
            0,
            1,
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name1", 1, "file"
                                ),
                            ),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name2", 2, "file"
                                ),
                                RawFrameData(
                                    "/path/to/file.py", "file.py", "name3", 3, "file"
                                ),
                            ),
                        )
                    ],
                ),
            ],
            {
                "frames": [
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name2",
                        "filename": "file.py",
                        "lineno": 2,
                        "module": "file",
                    },
                    {
                        "abs_path": "/path/to/file.py",
                        "function": "name3",
                        "filename": "file.py",
                        "lineno": 3,
                        "module": "file",
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "1",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                ],
                "stacks": [(0, 1)],
                "thread_metadata": thread_metadata,
            },
            id="wraps around buffer",
        ),
    ],
)
def test_sample_buffer(capacity, start_ns, stop_ns, samples, profile):
    buffer = SampleBuffer(capacity)
    for sample in samples:
        buffer.write(sample)
    result = buffer.slice_profile(start_ns, stop_ns)
    assert result == profile
