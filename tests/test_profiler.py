import inspect
import platform
import threading
from contextlib import contextmanager

import pytest

from sentry_sdk.profiler import (
    RawFrameData,
    SampleBuffer,
    extract_stack,
    get_frame_name,
    setup_profiler,
    teardown_profiler,
)
from sentry_sdk.utils import PY33

UNIX = platform.system().lower() in {"linux", "darwin"}


@contextmanager
def profiler(options):
    setup_profiler(options)
    yield
    teardown_profiler()


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

    @classmethod
    def class_method(cls):
        return inspect.currentframe()

    @staticmethod
    def static_method():
        return inspect.currentframe()


@pytest.mark.skipif(not PY33, reason="requires >=python3.3")
def test_profiler_invalid_mode():
    with pytest.raises(ValueError):
        setup_profiler({"_experiments": {"profiler_mode": "magic"}})
    # make sure to clean up at the end of the test
    teardown_profiler()


@pytest.mark.skipif(not PY33, reason="requires >=python3.3")
@pytest.mark.skipif(not UNIX, reason="requires UNIX platform")
@pytest.mark.parametrize("mode", ["sigprof", "sigalrm"])
def test_profiler_signal_mode_none_main_thread(mode):
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
    teardown_profiler()


@pytest.mark.skipif(not PY33, reason="requires >=python3.3")
@pytest.mark.parametrize("mode", ["sleep", "event", "sigprof", "sigalrm"])
def test_profiler_valid_mode(mode):
    # should not raise any exceptions
    with profiler({"_experiments": {"profiler_mode": mode}}):
        pass


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
            GetFrame().class_method(),
            "GetFrame.class_method",
            id="class_method",
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


@pytest.mark.parametrize(
    ("capacity", "start_ns", "stop_ns", "samples", "profile"),
    [
        pytest.param(
            10,
            0,
            1,
            [],
            {
                "stacks": [],
                "frames": [],
                "samples": [],
            },
            id="empty",
        ),
        pytest.param(
            10,
            0,
            1,
            [(2, [(1, [RawFrameData("name", "file", 1)])])],
            {
                "stacks": [],
                "frames": [],
                "samples": [],
            },
            id="single sample out of range",
        ),
        pytest.param(
            10,
            0,
            1,
            [(0, [(1, [RawFrameData("name", "file", 1)])])],
            {
                "stacks": [(0,)],
                "frames": [
                    {
                        "function": "name",
                        "filename": "file",
                        "lineno": 1,
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "0",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                ],
            },
            id="single sample in range",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (0, [(1, [RawFrameData("name", "file", 1)])]),
                (1, [(1, [RawFrameData("name", "file", 1)])]),
            ],
            {
                "stacks": [(0,)],
                "frames": [
                    {
                        "function": "name",
                        "filename": "file",
                        "lineno": 1,
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
            },
            id="two identical stacks",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (0, [(1, [RawFrameData("name1", "file", 1)])]),
                (1, [(1, [RawFrameData("name1", "file", 1), RawFrameData("name2", "file", 2)])]),
            ],
            {
                "stacks": [(0,), (0, 1)],
                "frames": [
                    {
                        "function": "name1",
                        "filename": "file",
                        "lineno": 1,
                    },
                    {
                        "function": "name2",
                        "filename": "file",
                        "lineno": 2,
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
            },
            id="two identical frames",
        ),
        pytest.param(
            10,
            0,
            1,
            [
                (0, [(1, [RawFrameData("name1", "file", 1), RawFrameData("name2", "file", 2)])]),
                (1, [(1, [RawFrameData("name3", "file", 3), RawFrameData("name4", "file", 4)])]),
            ],
            {
                "stacks": [(0, 1), (2, 3)],
                "frames": [
                    {
                        "function": "name1",
                        "filename": "file",
                        "lineno": 1,
                    },
                    {
                        "function": "name2",
                        "filename": "file",
                        "lineno": 2,
                    },
                    {
                        "function": "name3",
                        "filename": "file",
                        "lineno": 3,
                    },
                    {
                        "function": "name4",
                        "filename": "file",
                        "lineno": 4,
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
            },
            id="two unique stacks",
        ),
        pytest.param(
            1,
            0,
            1,
            [
                (0, [(1, [RawFrameData("name1", "file", 1)])]),
                (1, [(1, [RawFrameData("name2", "file", 2), RawFrameData("name3", "file", 3)])]),
            ],
            {
                "stacks": [(0, 1)],
                "frames": [
                    {
                        "function": "name2",
                        "filename": "file",
                        "lineno": 2,
                    },
                    {
                        "function": "name3",
                        "filename": "file",
                        "lineno": 3,
                    },
                ],
                "samples": [
                    {
                        "elapsed_since_start_ns": "1",
                        "thread_id": "1",
                        "stack_id": 0,
                    },
                ],
            },
            id="wraps around buffer",
        ),
    ],
)
def test_sample_buffer(capacity, start_ns, stop_ns, samples, profile):
    buffer = SampleBuffer(capacity)
    for sample in samples:
        buffer.write(sample)
    assert buffer.slice_profile(start_ns, stop_ns) == profile
