import inspect
import os
import sys
import threading

import pytest

from collections import defaultdict
from sentry_sdk import start_transaction
from sentry_sdk.profiler import (
    GeventScheduler,
    Profile,
    ThreadScheduler,
    extract_frame,
    extract_stack,
    get_current_thread_id,
    get_frame_name,
    setup_profiler,
)
from sentry_sdk.tracing import Transaction
from sentry_sdk._queue import Queue

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

try:
    import gevent
except ImportError:
    gevent = None


def requires_python_version(major, minor, reason=None):
    if reason is None:
        reason = "Requires Python {}.{}".format(major, minor)
    return pytest.mark.skipif(sys.version_info < (major, minor), reason=reason)


requires_gevent = pytest.mark.skipif(gevent is None, reason="gevent not enabled")


def process_test_sample(sample):
    # insert a mock hashable for the stack
    return [(tid, (stack, stack)) for tid, stack in sample]


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("foo"),
        pytest.param(
            "gevent",
            marks=pytest.mark.skipif(gevent is not None, reason="gevent not enabled"),
        ),
    ],
)
def test_profiler_invalid_mode(mode, teardown_profiling):
    with pytest.raises(ValueError):
        setup_profiler({"_experiments": {"profiler_mode": mode}})


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("sleep"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
def test_profiler_valid_mode(mode, teardown_profiling):
    # should not raise any exceptions
    setup_profiler({"_experiments": {"profiler_mode": mode}})


@requires_python_version(3, 3)
def test_profiler_setup_twice(teardown_profiling):
    # setting up the first time should return True to indicate success
    assert setup_profiler({"_experiments": {}})
    # setting up the second time should return False to indicate no-op
    assert not setup_profiler({"_experiments": {}})


@pytest.mark.parametrize(
    ("profiles_sample_rate", "profile_count"),
    [
        pytest.param(1.00, 1, id="profiler sampled at 1.00"),
        pytest.param(0.75, 1, id="profiler sampled at 0.75"),
        pytest.param(0.25, 0, id="profiler sampled at 0.25"),
        pytest.param(0.00, 0, id="profiler sampled at 0.00"),
        pytest.param(None, 0, id="profiler not enabled"),
    ],
)
@mock.patch("sentry_sdk.profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_profiled_transaction(
    sentry_init,
    capture_envelopes,
    teardown_profiling,
    profiles_sample_rate,
    profile_count,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": profiles_sample_rate},
    )

    envelopes = capture_envelopes()

    with mock.patch("sentry_sdk.profiler.random.random", return_value=0.5):
        with start_transaction(name="profiling"):
            pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile"]) == profile_count


@mock.patch("sentry_sdk.profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_profile_context(
    sentry_init,
    capture_envelopes,
    teardown_profiling,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": 1.0},
    )

    envelopes = capture_envelopes()

    with start_transaction(name="profiling"):
        pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile"]) == 1

    transaction = items["transaction"][0]
    profile = items["profile"][0]
    assert transaction.payload.json["contexts"]["profile"] == {
        "profile_id": profile.payload.json["event_id"],
    }


def test_minimum_unique_samples_required(
    sentry_init,
    capture_envelopes,
    teardown_profiling,
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"profiles_sample_rate": 1.0},
    )

    envelopes = capture_envelopes()

    with start_transaction(name="profiling"):
        pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    # because we dont leave any time for the profiler to
    # take any samples, it should be not be sent
    assert len(items["profile"]) == 0


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


class GetFrameBase:
    def inherited_instance_method(self):
        return inspect.currentframe()

    def inherited_instance_method_wrapped(self):
        def wrapped():
            return inspect.currentframe()

        return wrapped

    @classmethod
    def inherited_class_method(cls):
        return inspect.currentframe()

    @classmethod
    def inherited_class_method_wrapped(cls):
        def wrapped():
            return inspect.currentframe()

        return wrapped

    @staticmethod
    def inherited_static_method():
        return inspect.currentframe()


class GetFrame(GetFrameBase):
    def instance_method(self):
        return inspect.currentframe()

    def instance_method_wrapped(self):
        def wrapped():
            return inspect.currentframe()

        return wrapped

    @classmethod
    def class_method(cls):
        return inspect.currentframe()

    @classmethod
    def class_method_wrapped(cls):
        def wrapped():
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
            "wrapped"
            if sys.version_info < (3, 11)
            else "GetFrame.instance_method_wrapped.<locals>.wrapped",
            id="instance_method_wrapped",
        ),
        pytest.param(
            GetFrame().class_method(),
            "GetFrame.class_method",
            id="class_method",
        ),
        pytest.param(
            GetFrame().class_method_wrapped()(),
            "wrapped"
            if sys.version_info < (3, 11)
            else "GetFrame.class_method_wrapped.<locals>.wrapped",
            id="class_method_wrapped",
        ),
        pytest.param(
            GetFrame().static_method(),
            "static_method" if sys.version_info < (3, 11) else "GetFrame.static_method",
            id="static_method",
        ),
        pytest.param(
            GetFrame().inherited_instance_method(),
            "GetFrameBase.inherited_instance_method",
            id="inherited_instance_method",
        ),
        pytest.param(
            GetFrame().inherited_instance_method_wrapped()(),
            "wrapped"
            if sys.version_info < (3, 11)
            else "GetFrameBase.inherited_instance_method_wrapped.<locals>.wrapped",
            id="instance_method_wrapped",
        ),
        pytest.param(
            GetFrame().inherited_class_method(),
            "GetFrameBase.inherited_class_method",
            id="inherited_class_method",
        ),
        pytest.param(
            GetFrame().inherited_class_method_wrapped()(),
            "wrapped"
            if sys.version_info < (3, 11)
            else "GetFrameBase.inherited_class_method_wrapped.<locals>.wrapped",
            id="inherited_class_method_wrapped",
        ),
        pytest.param(
            GetFrame().inherited_static_method(),
            "inherited_static_method"
            if sys.version_info < (3, 11)
            else "GetFrameBase.inherited_static_method",
            id="inherited_static_method",
        ),
    ],
)
def test_get_frame_name(frame, frame_name):
    assert get_frame_name(frame) == frame_name


@pytest.mark.parametrize(
    ("get_frame", "function"),
    [
        pytest.param(lambda: get_frame(depth=1), "get_frame", id="simple"),
    ],
)
def test_extract_frame(get_frame, function):
    cwd = os.getcwd()
    frame = get_frame()
    extracted_frame = extract_frame(frame, cwd)

    # the abs_path should be equal toe the normalized path of the co_filename
    assert extracted_frame[0] == os.path.normpath(frame.f_code.co_filename)

    # the module should be pull from this test module
    assert extracted_frame[1] == __name__

    # the filename should be the file starting after the cwd
    assert extracted_frame[2] == __file__[len(cwd) + 1 :]

    assert extracted_frame[3] == function

    # the lineno will shift over time as this file is modified so just check
    # that it is an int
    assert isinstance(extracted_frame[4], int)


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
    _, stack, _ = extract_stack(
        frame, os.getcwd(), max_stack_depth=max_stack_depth + base_stack_depth
    )
    assert len(stack) == base_stack_depth + actual_depth

    for i in range(actual_depth):
        assert stack[i][3] == "get_frame", i

    # index 0 contains the inner most frame on the stack, so the lamdba
    # should be at index `actual_depth`
    if sys.version_info >= (3, 11):
        assert (
            stack[actual_depth][3]
            == "test_extract_stack_with_max_depth.<locals>.<lambda>"
        ), actual_depth
    else:
        assert stack[actual_depth][3] == "<lambda>", actual_depth


def test_extract_stack_with_cache():
    frame = get_frame(depth=1)

    prev_cache = extract_stack(frame, os.getcwd())
    _, stack1, _ = prev_cache
    _, stack2, _ = extract_stack(frame, os.getcwd(), prev_cache)

    assert len(stack1) == len(stack2)
    for i, (frame1, frame2) in enumerate(zip(stack1, stack2)):
        # DO NOT use `==` for the assertion here since we are
        # testing for identity, and using `==` would test for
        # equality which would always pass since we're extract
        # the same stack.
        assert frame1 is frame2, i


def test_get_current_thread_id_explicit_thread():
    results = Queue(maxsize=1)

    def target1():
        pass

    def target2():
        results.put(get_current_thread_id(thread1))

    thread1 = threading.Thread(target=target1)
    thread1.start()

    thread2 = threading.Thread(target=target2)
    thread2.start()

    thread2.join()
    thread1.join()

    assert thread1.ident == results.get(timeout=1)


@requires_gevent
def test_get_current_thread_id_gevent_in_thread():
    results = Queue(maxsize=1)

    def target():
        job = gevent.spawn(get_current_thread_id)
        job.join()
        results.put(job.value)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert thread.ident == results.get(timeout=1)


def test_get_current_thread_id_running_thread():
    results = Queue(maxsize=1)

    def target():
        results.put(get_current_thread_id())

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert thread.ident == results.get(timeout=1)


def test_get_current_thread_id_main_thread():
    results = Queue(maxsize=1)

    def target():
        # mock that somehow the current thread doesn't exist
        with mock.patch("threading.current_thread", side_effect=[None]):
            results.put(get_current_thread_id())

    thread_id = threading.main_thread().ident if sys.version_info >= (3, 4) else None

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    assert thread_id == results.get(timeout=1)


def get_scheduler_threads(scheduler):
    return [thread for thread in threading.enumerate() if thread.name == scheduler.name]


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(ThreadScheduler, id="thread scheduler"),
        pytest.param(
            GeventScheduler,
            marks=[
                requires_gevent,
                pytest.mark.skip(
                    reason="cannot find this thread via threading.enumerate()"
                ),
            ],
            id="gevent scheduler",
        ),
    ],
)
def test_thread_scheduler_single_background_thread(scheduler_class):
    scheduler = scheduler_class(frequency=1000)

    # not yet setup, no scheduler threads yet
    assert len(get_scheduler_threads(scheduler)) == 0

    scheduler.setup()

    # the scheduler will start always 1 thread
    assert len(get_scheduler_threads(scheduler)) == 1

    scheduler.teardown()

    # once finished, the thread should stop
    assert len(get_scheduler_threads(scheduler)) == 0


@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(ThreadScheduler, id="thread scheduler"),
        pytest.param(GeventScheduler, marks=requires_gevent, id="gevent scheduler"),
    ],
)
@mock.patch("sentry_sdk.profiler.MAX_PROFILE_DURATION_NS", 1)
def test_max_profile_duration_reached(scheduler_class):
    sample = [
        (
            "1",
            (("/path/to/file.py", "file", "file.py", "name", 1),),
        )
    ]

    with scheduler_class(frequency=1000) as scheduler:
        transaction = Transaction(sampled=True)
        with Profile(transaction, scheduler=scheduler) as profile:
            # profile just started, it's active
            assert profile.active

            # write a sample at the start time, so still active
            profile.write(profile.start_ns + 0, process_test_sample(sample))
            assert profile.active

            # write a sample at max time, so still active
            profile.write(profile.start_ns + 1, process_test_sample(sample))
            assert profile.active

            # write a sample PAST the max time, so now inactive
            profile.write(profile.start_ns + 2, process_test_sample(sample))
            assert not profile.active


current_thread = threading.current_thread()
thread_metadata = {
    str(current_thread.ident): {
        "name": str(current_thread.name),
    },
}


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        pytest.param(
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
            [
                (
                    6,
                    [
                        (
                            "1",
                            (("/path/to/file.py", "file", "file.py", "name", 1),),
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
            [
                (
                    0,
                    [
                        (
                            "1",
                            (("/path/to/file.py", "file", "file.py", "name", 1),),
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
                "stacks": [[0]],
                "thread_metadata": thread_metadata,
            },
            id="single sample in range",
        ),
        pytest.param(
            [
                (
                    0,
                    [
                        (
                            "1",
                            (("/path/to/file.py", "file", "file.py", "name", 1),),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (("/path/to/file.py", "file", "file.py", "name", 1),),
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
                "stacks": [[0]],
                "thread_metadata": thread_metadata,
            },
            id="two identical stacks",
        ),
        pytest.param(
            [
                (
                    0,
                    [
                        (
                            "1",
                            (("/path/to/file.py", "file", "file.py", "name1", 1),),
                        )
                    ],
                ),
                (
                    1,
                    [
                        (
                            "1",
                            (
                                ("/path/to/file.py", "file", "file.py", "name1", 1),
                                ("/path/to/file.py", "file", "file.py", "name2", 2),
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
                "stacks": [[0], [0, 1]],
                "thread_metadata": thread_metadata,
            },
            id="two identical frames",
        ),
        pytest.param(
            [
                (
                    0,
                    [
                        (
                            "1",
                            (
                                ("/path/to/file.py", "file", "file.py", "name1", 1),
                                (
                                    "/path/to/file.py",
                                    "file",
                                    "file.py",
                                    "name2",
                                    2,
                                    "file",
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
                                (
                                    "/path/to/file.py",
                                    "file",
                                    "file.py",
                                    "name3",
                                    3,
                                    "file",
                                ),
                                (
                                    "/path/to/file.py",
                                    "file",
                                    "file.py",
                                    "name4",
                                    4,
                                    "file",
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
                "stacks": [[0, 1], [2, 3]],
                "thread_metadata": thread_metadata,
            },
            id="two unique stacks",
        ),
    ],
)
@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(ThreadScheduler, id="thread scheduler"),
        pytest.param(GeventScheduler, marks=requires_gevent, id="gevent scheduler"),
    ],
)
@mock.patch("sentry_sdk.profiler.MAX_PROFILE_DURATION_NS", 5)
def test_profile_processing(
    DictionaryContaining,  # noqa: N803
    scheduler_class,
    samples,
    expected,
):
    with scheduler_class(frequency=1000) as scheduler:
        transaction = Transaction(sampled=True)
        with Profile(transaction, scheduler=scheduler) as profile:
            for ts, sample in samples:
                # force the sample to be written at a time relative to the
                # start of the profile
                now = profile.start_ns + ts
                profile.write(now, process_test_sample(sample))

            processed = profile.process()

            assert processed["thread_metadata"] == DictionaryContaining(
                expected["thread_metadata"]
            )
            assert processed["frames"] == expected["frames"]
            assert processed["stacks"] == expected["stacks"]
            assert processed["samples"] == expected["samples"]
