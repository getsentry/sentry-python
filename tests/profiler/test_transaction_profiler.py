import inspect
import os
import sys
import threading
import time
import warnings
from collections import defaultdict
from unittest import mock

import pytest

from sentry_sdk import start_span
from sentry_sdk.profiler.transaction_profiler import (
    GeventScheduler,
    Profile,
    Scheduler,
    ThreadScheduler,
    setup_profiler,
)
from sentry_sdk.profiler.utils import (
    extract_frame,
    extract_stack,
    frame_id,
    get_frame_name,
)
from sentry_sdk._lru_cache import LRUCache

try:
    import gevent
except ImportError:
    gevent = None


requires_gevent = pytest.mark.skipif(gevent is None, reason="gevent not enabled")


def process_test_sample(sample):
    # insert a mock hashable for the stack
    return [(tid, (stack, stack)) for tid, stack in sample]


@pytest.mark.parametrize(
    "mode",
    [pytest.param("foo")],
)
def test_profiler_invalid_mode(mode, teardown_profiling):
    with pytest.raises(ValueError):
        setup_profiler({"profiler_mode": mode})


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
    setup_profiler({"profiler_mode": mode})


def test_profiler_setup_twice(teardown_profiling):
    # setting up the first time should return True to indicate success
    assert setup_profiler({})
    # setting up the second time should return False to indicate no-op
    assert not setup_profiler({})


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
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
@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_profiles_sample_rate(
    sentry_init,
    capture_envelopes,
    capture_record_lost_event_calls,
    teardown_profiling,
    profiles_sample_rate,
    profile_count,
    mode,
):
    sentry_init(
        traces_sample_rate=1.0,
        profiler_mode=mode,
        profiles_sample_rate=profiles_sample_rate,
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with mock.patch(
        "sentry_sdk.profiler.transaction_profiler.random.random", return_value=0.5
    ):
        with start_span(name="profiling"):
            pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile"]) == profile_count
    if profiles_sample_rate is None or profiles_sample_rate == 0:
        assert record_lost_event_calls == []
    elif profile_count:
        assert record_lost_event_calls == []
    else:
        assert record_lost_event_calls == [("sample_rate", "profile", None, 1)]


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    ("profiles_sampler", "profile_count"),
    [
        pytest.param(lambda _: 1.00, 1, id="profiler sampled at 1.00"),
        pytest.param(lambda _: 0.75, 1, id="profiler sampled at 0.75"),
        pytest.param(lambda _: 0.25, 0, id="profiler sampled at 0.25"),
        pytest.param(lambda _: 0.00, 0, id="profiler sampled at 0.00"),
        pytest.param(lambda _: None, 0, id="profiler not enabled"),
        pytest.param(
            lambda ctx: 1 if ctx["transaction_context"]["name"] == "profiling" else 0,
            1,
            id="profiler sampled for transaction name",
        ),
        pytest.param(
            lambda ctx: 0 if ctx["transaction_context"]["name"] == "profiling" else 1,
            0,
            id="profiler not sampled for transaction name",
        ),
        pytest.param(
            lambda _: "1", 0, id="profiler not sampled because string sample rate"
        ),
        pytest.param(lambda _: True, 1, id="profiler sampled at True"),
        pytest.param(lambda _: False, 0, id="profiler sampled at False"),
    ],
)
@mock.patch("sentry_sdk.profiler.transaction_profiler.PROFILE_MINIMUM_SAMPLES", 0)
def test_profiles_sampler(
    sentry_init,
    capture_envelopes,
    capture_record_lost_event_calls,
    teardown_profiling,
    profiles_sampler,
    profile_count,
    mode,
):
    sentry_init(
        traces_sample_rate=1.0,
        profiles_sampler=profiles_sampler,
        profiler_mode=mode,
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with mock.patch(
        "sentry_sdk.profiler.transaction_profiler.random.random", return_value=0.5
    ):
        with start_span(name="profiling"):
            pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile"]) == profile_count
    if profile_count:
        assert record_lost_event_calls == []
    else:
        assert record_lost_event_calls == [("sample_rate", "profile", None, 1)]


def test_minimum_unique_samples_required(
    sentry_init,
    capture_envelopes,
    capture_record_lost_event_calls,
    teardown_profiling,
):
    sentry_init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with start_span(name="profiling"):
        pass

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    # because we dont leave any time for the profiler to
    # take any samples, it should be not be sent
    assert len(items["profile"]) == 0
    assert record_lost_event_calls == [("insufficient_data", "profile", None, 1)]


@pytest.mark.forked
def test_profile_captured(
    sentry_init,
    capture_envelopes,
    teardown_profiling,
):
    sentry_init(
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

    envelopes = capture_envelopes()

    with start_span(name="profiling"):
        time.sleep(0.05)

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile"]) == 1


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
            (
                "wrapped"
                if sys.version_info < (3, 11)
                else "GetFrame.instance_method_wrapped.<locals>.wrapped"
            ),
            id="instance_method_wrapped",
        ),
        pytest.param(
            GetFrame().class_method(),
            "GetFrame.class_method",
            id="class_method",
        ),
        pytest.param(
            GetFrame().class_method_wrapped()(),
            (
                "wrapped"
                if sys.version_info < (3, 11)
                else "GetFrame.class_method_wrapped.<locals>.wrapped"
            ),
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
            (
                "wrapped"
                if sys.version_info < (3, 11)
                else "GetFrameBase.inherited_instance_method_wrapped.<locals>.wrapped"
            ),
            id="instance_method_wrapped",
        ),
        pytest.param(
            GetFrame().inherited_class_method(),
            "GetFrameBase.inherited_class_method",
            id="inherited_class_method",
        ),
        pytest.param(
            GetFrame().inherited_class_method_wrapped()(),
            (
                "wrapped"
                if sys.version_info < (3, 11)
                else "GetFrameBase.inherited_class_method_wrapped.<locals>.wrapped"
            ),
            id="inherited_class_method_wrapped",
        ),
        pytest.param(
            GetFrame().inherited_static_method(),
            (
                "inherited_static_method"
                if sys.version_info < (3, 11)
                else "GetFrameBase.inherited_static_method"
            ),
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
    extracted_frame = extract_frame(frame_id(frame), frame, cwd)

    # the abs_path should be equal toe the normalized path of the co_filename
    assert extracted_frame["abs_path"] == os.path.normpath(frame.f_code.co_filename)

    # the module should be pull from this test module
    assert extracted_frame["module"] == __name__

    # the filename should be the file starting after the cwd
    assert extracted_frame["filename"] == __file__[len(cwd) + 1 :]

    assert extracted_frame["function"] == function

    # the lineno will shift over time as this file is modified so just check
    # that it is an int
    assert isinstance(extracted_frame["lineno"], int)


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
    _, frame_ids, frames = extract_stack(
        frame,
        LRUCache(max_size=1),
        max_stack_depth=max_stack_depth + base_stack_depth,
        cwd=os.getcwd(),
    )
    assert len(frame_ids) == base_stack_depth + actual_depth
    assert len(frames) == base_stack_depth + actual_depth

    for i in range(actual_depth):
        assert frames[i]["function"] == "get_frame", i

    # index 0 contains the inner most frame on the stack, so the lamdba
    # should be at index `actual_depth`
    if sys.version_info >= (3, 11):
        assert (
            frames[actual_depth]["function"]
            == "test_extract_stack_with_max_depth.<locals>.<lambda>"
        ), actual_depth
    else:
        assert frames[actual_depth]["function"] == "<lambda>", actual_depth


@pytest.mark.parametrize(
    ("frame", "depth"),
    [(get_frame(depth=1), len(inspect.stack()))],
)
def test_extract_stack_with_cache(frame, depth):
    # make sure cache has enough room or this test will fail
    cache = LRUCache(max_size=depth)
    cwd = os.getcwd()
    _, _, frames1 = extract_stack(frame, cache, cwd=cwd)
    _, _, frames2 = extract_stack(frame, cache, cwd=cwd)

    assert len(frames1) > 0
    assert len(frames2) > 0
    assert len(frames1) == len(frames2)
    for i, (frame1, frame2) in enumerate(zip(frames1, frames2)):
        # DO NOT use `==` for the assertion here since we are
        # testing for identity, and using `==` would test for
        # equality which would always pass since we're extract
        # the same stack.
        assert frame1 is frame2, i


def get_scheduler_threads(scheduler):
    return [thread for thread in threading.enumerate() if thread.name == scheduler.name]


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

    # setup but no profiles started so still no threads
    assert len(get_scheduler_threads(scheduler)) == 0

    scheduler.ensure_running()

    # the scheduler will start always 1 thread
    assert len(get_scheduler_threads(scheduler)) == 1

    scheduler.ensure_running()

    # the scheduler still only has 1 thread
    assert len(get_scheduler_threads(scheduler)) == 1

    scheduler.teardown()

    # once finished, the thread should stop
    assert len(get_scheduler_threads(scheduler)) == 0


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
def test_thread_scheduler_no_thread_on_shutdown(scheduler_class):
    scheduler = scheduler_class(frequency=1000)

    # not yet setup, no scheduler threads yet
    assert len(get_scheduler_threads(scheduler)) == 0

    scheduler.setup()

    # setup but no profiles started so still no threads
    assert len(get_scheduler_threads(scheduler)) == 0

    # mock RuntimeError as if the 3.12 intepreter was shutting down
    with mock.patch(
        "threading.Thread.start",
        side_effect=RuntimeError("can't create new thread at interpreter shutdown"),
    ):
        scheduler.ensure_running()

    assert scheduler.running is False

    # still no thread
    assert len(get_scheduler_threads(scheduler)) == 0

    scheduler.teardown()

    assert len(get_scheduler_threads(scheduler)) == 0


@pytest.mark.parametrize(
    ("scheduler_class",),
    [
        pytest.param(ThreadScheduler, id="thread scheduler"),
        pytest.param(GeventScheduler, marks=requires_gevent, id="gevent scheduler"),
    ],
)
@mock.patch("sentry_sdk.profiler.transaction_profiler.MAX_PROFILE_DURATION_NS", 1)
def test_max_profile_duration_reached(scheduler_class):
    sample = [
        (
            "1",
            extract_stack(
                get_frame(),
                LRUCache(max_size=1),
                cwd=os.getcwd(),
            ),
        ),
    ]

    with scheduler_class(frequency=1000) as scheduler:
        with Profile(True, 0, scheduler=scheduler) as profile:
            # profile just started, it's active
            assert profile.active

            # write a sample at the start time, so still active
            profile.write(profile.start_ns + 0, sample)
            assert profile.active

            # write a sample at max time, so still active
            profile.write(profile.start_ns + 1, sample)
            assert profile.active

            # write a sample PAST the max time, so now inactive
            profile.write(profile.start_ns + 2, sample)
            assert not profile.active


class NoopScheduler(Scheduler):
    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def ensure_running(self) -> None:
        pass


current_thread = threading.current_thread()
thread_metadata = {
    str(current_thread.ident): {
        "name": str(current_thread.name),
    },
}


sample_stacks = [
    extract_stack(
        get_frame(),
        LRUCache(max_size=1),
        max_stack_depth=1,
        cwd=os.getcwd(),
    ),
    extract_stack(
        get_frame(),
        LRUCache(max_size=1),
        max_stack_depth=2,
        cwd=os.getcwd(),
    ),
]


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
            [(6, [("1", sample_stacks[0])])],
            {
                "frames": [],
                "samples": [],
                "stacks": [],
                "thread_metadata": thread_metadata,
            },
            id="single sample out of range",
        ),
        pytest.param(
            [(0, [("1", sample_stacks[0])])],
            {
                "frames": [sample_stacks[0][2][0]],
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
                (0, [("1", sample_stacks[0])]),
                (1, [("1", sample_stacks[0])]),
            ],
            {
                "frames": [sample_stacks[0][2][0]],
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
                (0, [("1", sample_stacks[0])]),
                (1, [("1", sample_stacks[1])]),
            ],
            {
                "frames": [
                    sample_stacks[0][2][0],
                    sample_stacks[1][2][0],
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
                "stacks": [[0], [1, 0]],
                "thread_metadata": thread_metadata,
            },
            id="two different stacks",
        ),
    ],
)
@mock.patch("sentry_sdk.profiler.transaction_profiler.MAX_PROFILE_DURATION_NS", 5)
def test_profile_processing(
    DictionaryContaining,  # noqa: N803
    samples,
    expected,
):
    with NoopScheduler(frequency=1000) as scheduler:
        with Profile(True, 0, scheduler=scheduler) as profile:
            for ts, sample in samples:
                # force the sample to be written at a time relative to the
                # start of the profile
                now = profile.start_ns + ts
                profile.write(now, sample)

            processed = profile.process()

            assert processed["thread_metadata"] == DictionaryContaining(
                expected["thread_metadata"]
            )
            assert processed["frames"] == expected["frames"]
            assert processed["stacks"] == expected["stacks"]
            assert processed["samples"] == expected["samples"]


def test_no_warning_without_hub():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Profile(True, 0)
