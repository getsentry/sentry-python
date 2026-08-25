import os
import sys
import time
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk._span_batcher import SpanBatcher


def test_envelope_by_trace_id(sentry_init, capture_envelopes, monkeypatch):
    """Envelopes only contain spans of one trace ID."""
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.new_scope():
        sentry_sdk.traces.new_trace()
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        parent_a = sentry_sdk.traces.start_span(name="parent a")
        with sentry_sdk.traces.start_span(
            name="span 1a", parent_span=parent_a
        ) as span1:
            trace_id1 = span1.trace_id
        with sentry_sdk.traces.start_span(name="span 1b", parent_span=parent_a):
            pass

    with sentry_sdk.new_scope():
        sentry_sdk.traces.new_trace()
        parent_b = sentry_sdk.traces.start_span(name="parent b")
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        with sentry_sdk.traces.start_span(
            name="span 2a", parent_span=parent_b
        ) as span2:
            trace_id2 = span2.trace_id
        with sentry_sdk.traces.start_span(name="span 2b", parent_span=parent_b):
            pass

    sentry_sdk.flush()

    assert len(envelopes) == 2

    assert envelopes[0].headers["trace"]["trace_id"] == trace_id1
    assert len(envelopes[0].items[0].payload.json["items"]) == 2
    assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1a"
    assert envelopes[0].items[0].payload.json["items"][0]["trace_id"] == trace_id1
    assert envelopes[0].items[0].payload.json["items"][1]["name"] == "span 1b"
    assert envelopes[0].items[0].payload.json["items"][1]["trace_id"] == trace_id1

    assert envelopes[1].headers["trace"]["trace_id"] == trace_id2
    assert len(envelopes[1].items[0].payload.json["items"]) == 2
    assert envelopes[1].items[0].payload.json["items"][0]["name"] == "span 2a"
    assert envelopes[1].items[0].payload.json["items"][0]["trace_id"] == trace_id2
    assert envelopes[1].items[0].payload.json["items"][1]["name"] == "span 2b"
    assert envelopes[1].items[0].payload.json["items"][1]["trace_id"] == trace_id2


def test_max_envelope_size(sentry_init, capture_envelopes, monkeypatch):
    """Envelope max size is respected."""
    monkeypatch.setattr(SpanBatcher, "MAX_ENVELOPE_SIZE", 2)
    # set the time-based flush limit to something huge so that we're not observing
    # time-based flushes
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span 1"):
            pass
        with sentry_sdk.traces.start_span(name="span 2"):
            pass
        with sentry_sdk.traces.start_span(name="span 3"):
            pass
        with sentry_sdk.traces.start_span(name="span 4"):
            pass
        with sentry_sdk.traces.start_span(name="span 5"):
            pass

        sentry_sdk.flush()

        assert len(envelopes) == 3

        assert len(envelopes[0].items[0].payload.json["items"]) == 2
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"
        assert envelopes[0].items[0].payload.json["items"][1]["name"] == "span 2"
        assert len(envelopes[1].items[0].payload.json["items"]) == 2
        assert envelopes[1].items[0].payload.json["items"][0]["name"] == "span 3"
        assert envelopes[1].items[0].payload.json["items"][1]["name"] == "span 4"
        assert len(envelopes[2].items[0].payload.json["items"]) == 1
        assert envelopes[2].items[0].payload.json["items"][0]["name"] == "span 5"


def test_drop_after_max_reached(
    sentry_init, capture_envelopes, capture_record_lost_event_calls, monkeypatch
):
    """New spans are dropped if a bucket reaches MAX_BEFORE_DROP spans."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_DROP", 2)
    # set the time-based flush limit to something huge so that we're not flushing
    # prematurely
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span 1"):
            pass
        with sentry_sdk.traces.start_span(name="span 2"):
            pass
        with sentry_sdk.traces.start_span(name="span 3"):
            pass

        sentry_sdk.flush()

        assert len(envelopes) == 1

        assert len(envelopes[0].items[0].payload.json["items"]) == 2
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"
        assert envelopes[0].items[0].payload.json["items"][1]["name"] == "span 2"

        assert ("queue_overflow", "span", None, 1) in record_lost_event_calls


def test_drop_isolated_per_bucket(
    sentry_init, capture_envelopes, capture_record_lost_event_calls, monkeypatch
):
    """A bucket reaching MAX_BEFORE_DROP only drops spans from that trace."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_DROP", 2)
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.new_scope():
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        parent_a = sentry_sdk.traces.start_span(name="parent a")
        with sentry_sdk.traces.start_span(name="a1", parent_span=parent_a) as span_a:
            trace_id_a = span_a.trace_id
        with sentry_sdk.traces.start_span(name="a2", parent_span=parent_a):
            pass
        with sentry_sdk.traces.start_span(name="a3"):
            pass

    with sentry_sdk.new_scope():
        sentry_sdk.traces.new_trace()
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        parent_b = sentry_sdk.traces.start_span(name="parent b")
        with sentry_sdk.traces.start_span(name="b1", parent_span=parent_b) as span_b:
            trace_id_b = span_b.trace_id
        with sentry_sdk.traces.start_span(name="b2", parent_span=parent_b):
            pass

    sentry_sdk.flush()

    assert len(envelopes) == 2

    assert envelopes[0].headers["trace"]["trace_id"] == trace_id_a
    assert len(envelopes[0].items[0].payload.json["items"]) == 2
    assert envelopes[0].items[0].payload.json["items"][0]["name"] == "a1"
    assert envelopes[0].items[0].payload.json["items"][1]["name"] == "a2"

    assert envelopes[1].headers["trace"]["trace_id"] == trace_id_b
    assert len(envelopes[1].items[0].payload.json["items"]) == 2
    assert envelopes[1].items[0].payload.json["items"][0]["name"] == "b1"
    assert envelopes[1].items[0].payload.json["items"][1]["name"] == "b2"

    assert record_lost_event_calls.count(("queue_overflow", "span", None, 1)) == 1


def test_length_based_flushing(sentry_init, capture_items, monkeypatch):
    """A flush event is triggered when a bucket contains MAX_BEFORE_FLUSH spans."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 1)
    # set the time-based flush limit to something huge so that we're not hitting
    # it since we want to test MAX_BEFORE_FLUSH instead
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span"):
            pass

        time.sleep(0.1)

        assert len(items) == 1
        assert items[0].payload["name"] == "span"


def test_weight_based_flushing(sentry_init, capture_envelopes, monkeypatch):
    """When a bucket reaches MAX_BYTES_BEFORE_FLUSH, it'll be flushed."""
    monkeypatch.setattr(SpanBatcher, "MAX_BYTES_BEFORE_FLUSH", 1)
    # set the time-based flush limit to something huge so that it doesn't
    # interfere
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span"):
            pass

        time.sleep(0.1)

        assert len(envelopes) == 1

        assert len(envelopes[0].items[0].payload.json["items"]) == 1
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span"


def test_weight_based_flushing_by_attribute_size(
    sentry_init, capture_envelopes, monkeypatch
):
    """Attribute size is taken into account when using MAX_BYTES_BEFORE_FLUSH."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="small span") as bare_span:
            pass

        bare_span_size = SpanBatcher._estimate_size(bare_span._to_json())
        big_attr = "x" * bare_span_size

        monkeypatch.setattr(SpanBatcher, "MAX_BYTES_BEFORE_FLUSH", bare_span_size * 3)

        time.sleep(0.1)

        # The first span alone is well under the byte limit, so no flush yet.
        assert len(envelopes) == 0

        with sentry_sdk.traces.start_span(
            name="big span", attributes={"big": big_attr}
        ):
            pass

        time.sleep(0.1)

        assert len(envelopes) == 1
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "small span"
        assert envelopes[0].items[0].payload.json["items"][1]["name"] == "big span"


def test_bucket_recreated_after_flush(sentry_init, capture_envelopes, monkeypatch):
    """Spans for a trace that arrive after that trace's bucket was flushed land in a fresh bucket."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 2)
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span 1") as span1:
            trace_id = span1.trace_id
        with sentry_sdk.traces.start_span(name="span 2"):
            pass

        time.sleep(0.1)

        assert len(envelopes) == 1

        with sentry_sdk.traces.start_span(name="span 3"):
            pass
        with sentry_sdk.traces.start_span(name="span 4"):
            pass

        time.sleep(0.1)

        assert len(envelopes) == 2

        assert envelopes[0].headers["trace"]["trace_id"] == trace_id
        assert len(envelopes[0].items[0].payload.json["items"]) == 2
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"
        assert envelopes[0].items[0].payload.json["items"][0]["trace_id"] == trace_id
        assert envelopes[0].items[0].payload.json["items"][1]["name"] == "span 2"
        assert envelopes[0].items[0].payload.json["items"][1]["trace_id"] == trace_id

        assert envelopes[1].headers["trace"]["trace_id"] == trace_id
        assert len(envelopes[1].items[0].payload.json["items"]) == 2
        assert envelopes[1].items[0].payload.json["items"][0]["name"] == "span 3"
        assert envelopes[1].items[0].payload.json["items"][0]["trace_id"] == trace_id
        assert envelopes[1].items[0].payload.json["items"][1]["name"] == "span 4"
        assert envelopes[1].items[0].payload.json["items"][1]["trace_id"] == trace_id


def test_quiet_buckets_flush_eventually(sentry_init, capture_envelopes, monkeypatch):
    """Even if a bucket doesn't trigger any size limits, it'll get flushed eventually."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 0.1)
    # these are purposefully high so as to never trigger in this test scenario
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 10000000)
    monkeypatch.setattr(SpanBatcher, "MAX_BYTES_BEFORE_FLUSH", 10000000)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="custom parent"):
        with sentry_sdk.traces.start_span(name="span 1"):
            pass

        time.sleep(0.3)

        assert len(envelopes) == 1

        assert len(envelopes[0].items[0].payload.json["items"]) == 1
        assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"


def test_quiet_buckets_flushed_with_busy_neighbors(
    sentry_init, capture_envelopes, monkeypatch
):
    """When there are both busy and quiet buckets, all buckets get flushed eventually."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 0.1)
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 5)

    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    sentry_sdk.traces.new_trace()

    with sentry_sdk.new_scope():
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        parent_a = sentry_sdk.traces.start_span(name="parent a")
        with sentry_sdk.traces.start_span(name="span 1", parent_span=parent_a) as span1:
            trace_id1 = span1.trace_id

    with sentry_sdk.new_scope():
        sentry_sdk.traces.new_trace()
        # Keep parent open as its bucket in the batcher would be emptied when it is finished.
        parent_b = sentry_sdk.traces.start_span(name="parent b")
        with sentry_sdk.traces.start_span(name="span 2", parent_span=parent_b) as span2:
            trace_id2 = span2.trace_id

        for i in range(3, 10):
            with sentry_sdk.traces.start_span(name=f"span {i}", parent_span=parent_b):
                pass

    time.sleep(0.3)

    assert len(envelopes) >= 2

    # we don't care how exactly the spans are distributed over envelopes
    # (since both the time and length based limits are effective, the
    # distribution might not be stable); just check whether the spans are all
    # there and that each envelope only contains spans from one trace
    seen = set()
    for envelope in envelopes:
        assert envelope.headers["trace"]["trace_id"] in (trace_id1, trace_id2)

        if envelope.headers["trace"]["trace_id"] == trace_id1:
            assert len(envelope.items[0].payload.json["items"]) == 1
            assert envelope.items[0].payload.json["items"][0]["name"] == "span 1"
            seen.add(envelope.items[0].payload.json["items"][0]["name"])

        elif envelope.headers["trace"]["trace_id"] == trace_id2:
            for span in envelope.items[0].payload.json["items"]:
                seen.add(span["name"])

    assert seen == {f"span {i}" for i in range(1, 10)}


def test_transport_format(sentry_init, capture_envelopes):
    sentry_init(
        server_name="test-server",
        release="1.0.0",
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="test"):
        ...

    sentry_sdk.get_client().flush()

    assert len(envelopes) == 1
    assert len(envelopes[0].items) == 1
    item = envelopes[0].items[0]

    assert item.type == "span"
    assert item.headers == {
        "type": "span",
        "item_count": 1,
        "content_type": "application/vnd.sentry.items.span.v2+json",
    }
    assert item.payload.json == {
        "version": 2,
        "items": [
            {
                "trace_id": mock.ANY,
                "span_id": mock.ANY,
                "name": "test",
                "status": "ok",
                "is_segment": True,
                "start_timestamp": mock.ANY,
                "end_timestamp": mock.ANY,
                "attributes": mock.ANY,
            }
        ],
    }
    for attribute, value in item.payload.json["items"][0]["attributes"].items():
        assert isinstance(attribute, str)

        assert isinstance(value, dict)
        assert (
            len(value) == 2
        )  # technically, "unit" is also supported, but we don't currently set it anywhere
        assert "value" in value
        assert "type" in value
        assert value["type"] in ("string", "boolean", "integer", "double", "array")


def test_trace_bucket_flushes_when_segment_ends(
    sentry_init, capture_items, monkeypatch
):
    """All currently completed spans in a trace are flushed when the segment is finished."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 100000)

    sentry_init(traces_sample_rate=1.0, trace_lifecycle="stream")
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="segment span"):
        with sentry_sdk.traces.start_span(name="child"):
            pass

    time.sleep(0.1)

    assert len(items) == 2
    assert items[0].payload["name"] == "child"
    assert items[1].payload["name"] == "segment span"


@pytest.mark.skipif(
    sys.platform == "win32"
    or not hasattr(os, "fork")
    or not hasattr(os, "register_at_fork"),
    reason="requires POSIX fork and os.register_at_fork (Python 3.7+)",
)
def test_span_batcher_lock_reset_in_child_after_fork(sentry_init):
    """Regression test for the SpanBatcher fork-deadlock fix.

    If os.fork() runs while another thread holds SpanBatcher._lock, the
    child inherits the lock locked. The holding thread does not exist in
    the child, so the lock can never be released and _ensure_thread
    deadlocks forever. The after-fork hook must replace the lock with a
    fresh one in the child and reset
    _flusher / _flusher_pid / _span_buffer / _running_size / _active /
    _flush_event.
    """
    sentry_init(
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    batcher = sentry_sdk.get_client().span_batcher
    assert batcher is not None

    original_lock = batcher._lock
    original_lock.acquire()

    batcher._span_buffer["test-trace-id"].append(object())
    batcher._running_size["test-trace-id"] = 42
    batcher._active.flag = True
    batcher._flush_event.set()
    batcher._running = False

    pid = os.fork()
    if pid == 0:
        replaced = batcher._lock is not original_lock
        unheld = batcher._lock.acquire(blocking=False)

        flusher_reset = batcher._flusher is None and batcher._flusher_pid is None
        span_buffer_reset = len(batcher._span_buffer) == 0
        running_size_reset = len(batcher._running_size) == 0

        active_reset = not getattr(batcher._active, "flag", False)
        event_reset = not batcher._flush_event.is_set()
        running_reset = batcher._running is True

        os._exit(
            0
            if replaced
            and unheld
            and flusher_reset
            and span_buffer_reset
            and running_size_reset
            and active_reset
            and event_reset
            and running_reset
            else 1
        )

    original_lock.release()
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


@pytest.mark.tests_internal_exceptions
def test_flush_loop_swallows_flush_exception():
    """The flush loop must not let one failed flush kill the flusher thread.

    Regression test for #7138: an unhandled exception inside _flush_loop
    terminated the daemon flusher thread. After that the span buffer filled up
    with nothing draining it, and every later span was dropped for the rest of
    the process lifetime. The loop must swallow the error and keep running.

    Driven synchronously on a bare batcher: _flush raises once and then stops
    the loop, so _flush_loop returns cleanly on fixed code and propagates the
    exception on unfixed code.
    """
    calls = []

    class ExplodingSpanBatcher(SpanBatcher):
        def _flush(self, only_pending=False):
            calls.append(1)
            self._running = False  # exit the loop after this one iteration
            raise RuntimeError("boom in flush")

    batcher = ExplodingSpanBatcher(
        capture_func=lambda envelope: None,
        record_lost_func=lambda *a, **k: None,
    )
    batcher._flush_event.set()  # so the loop's wait() returns at once
    batcher._flush_loop()

    assert calls == [1]
