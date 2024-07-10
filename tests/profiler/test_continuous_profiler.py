import threading
import time
from collections import defaultdict
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.profiler.continuous_profiler import (
    setup_continuous_profiler,
    start_profiler,
    stop_profiler,
)
from tests.conftest import ApproxDict

try:
    import gevent
except ImportError:
    gevent = None


requires_gevent = pytest.mark.skipif(gevent is None, reason="gevent not enabled")


def experimental_options(mode=None, auto_start=None):
    return {
        "_experiments": {
            "continuous_profiling_auto_start": auto_start,
            "continuous_profiling_mode": mode,
        }
    }


@pytest.mark.parametrize("mode", [pytest.param("foo")])
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_continuous_profiler_invalid_mode(mode, make_options, teardown_profiling):
    with pytest.raises(ValueError):
        setup_continuous_profiler(make_options(mode=mode), lambda envelope: None)


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_continuous_profiler_valid_mode(mode, make_options, teardown_profiling):
    options = make_options(mode=mode)
    setup_continuous_profiler(options, lambda envelope: None)


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_continuous_profiler_setup_twice(mode, make_options, teardown_profiling):
    options = make_options(mode=mode)
    # setting up the first time should return True to indicate success
    assert setup_continuous_profiler(options, lambda envelope: None)
    # setting up the second time should return False to indicate no-op
    assert not setup_continuous_profiler(options, lambda envelope: None)


def assert_single_transaction_with_profile_chunks(envelopes, thread):
    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile_chunk"]) > 0

    transaction = items["transaction"][0].payload.json

    trace_context = transaction["contexts"]["trace"]

    assert trace_context == ApproxDict(
        {
            "data": ApproxDict(
                {
                    "thread.id": str(thread.ident),
                    "thread.name": thread.name,
                }
            ),
        }
    )

    profile_context = transaction["contexts"]["profile"]
    profiler_id = profile_context["profiler_id"]

    assert profile_context == ApproxDict({"profiler_id": profiler_id})

    spans = transaction["spans"]
    assert len(spans) > 0
    for span in spans:
        assert span["data"] == ApproxDict(
            {
                "profiler_id": profiler_id,
                "thread.id": str(thread.ident),
                "thread.name": thread.name,
            }
        )

    for profile_chunk_item in items["profile_chunk"]:
        profile_chunk = profile_chunk_item.payload.json
        assert profile_chunk == ApproxDict(
            {"platform": "python", "profiler_id": profiler_id, "version": "2"}
        )


def assert_single_transaction_without_profile_chunks(envelopes):
    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile_chunk"]) == 0

    transaction = items["transaction"][0].payload.json
    assert "profile" not in transaction["contexts"]


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
@mock.patch("sentry_sdk.profiler.continuous_profiler.PROFILE_BUFFER_SECONDS", 0.01)
def test_continuous_profiler_auto_start_and_manual_stop(
    sentry_init,
    capture_envelopes,
    mode,
    make_options,
    teardown_profiling,
):
    options = make_options(mode=mode, auto_start=True)
    sentry_init(
        traces_sample_rate=1.0,
        _experiments=options.get("_experiments", {}),
    )

    envelopes = capture_envelopes()

    thread = threading.current_thread()

    with sentry_sdk.start_transaction(name="profiling"):
        with sentry_sdk.start_span(op="op"):
            time.sleep(0.05)

    assert_single_transaction_with_profile_chunks(envelopes, thread)

    for _ in range(3):
        stop_profiler()

        envelopes.clear()

        with sentry_sdk.start_transaction(name="profiling"):
            with sentry_sdk.start_span(op="op"):
                time.sleep(0.05)

        assert_single_transaction_without_profile_chunks(envelopes)

        start_profiler()

        envelopes.clear()

        with sentry_sdk.start_transaction(name="profiling"):
            with sentry_sdk.start_span(op="op"):
                time.sleep(0.05)

        assert_single_transaction_with_profile_chunks(envelopes, thread)


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
@mock.patch("sentry_sdk.profiler.continuous_profiler.PROFILE_BUFFER_SECONDS", 0.01)
def test_continuous_profiler_manual_start_and_stop(
    sentry_init,
    capture_envelopes,
    mode,
    make_options,
    teardown_profiling,
):
    options = make_options(mode=mode)
    sentry_init(
        traces_sample_rate=1.0,
        _experiments=options.get("_experiments", {}),
    )

    envelopes = capture_envelopes()

    thread = threading.current_thread()

    for _ in range(3):
        start_profiler()

        envelopes.clear()

        with sentry_sdk.start_transaction(name="profiling"):
            with sentry_sdk.start_span(op="op"):
                time.sleep(0.05)

        assert_single_transaction_with_profile_chunks(envelopes, thread)

        stop_profiler()

        envelopes.clear()

        with sentry_sdk.start_transaction(name="profiling"):
            with sentry_sdk.start_span(op="op"):
                time.sleep(0.05)

        assert_single_transaction_without_profile_chunks(envelopes)
