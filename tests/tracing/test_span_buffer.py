import time

import sentry_sdk
from sentry_sdk._span_batcher import SpanBatcher


def test_envelope_by_trace_id(sentry_init, capture_envelopes, monkeypatch):
    """Envelopes only contain spans of one trace ID."""
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span 1a") as span1:
        trace_id1 = span1.trace_id
    with sentry_sdk.traces.start_span(name="span 1b"):
        pass

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span 2a") as span2:
        trace_id2 = span2.trace_id
    with sentry_sdk.traces.start_span(name="span 2b"):
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


def test_length_based_flushing(sentry_init, capture_items, monkeypatch):
    """A flush event is triggered when a bucket contains MAX_BEFORE_FLUSH spans."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 1)
    # set the time-based flush limit to something huge so that we're not hitting
    # it since we want to test MAX_BEFORE_FLUSH instead
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 1000)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="span"):
        pass

    time.sleep(0.5)

    assert len(items) == 1
    assert items[0].payload["name"] == "span"


def test_max_envelope_size(sentry_init, capture_envelopes, monkeypatch):
    """Envelope max size is respected."""
    monkeypatch.setattr(SpanBatcher, "MAX_ENVELOPE_SIZE", 2)
    # set the time-based flush limit to something huge so that we're not hitting
    # it
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 1000)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

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


def test_drop_after_max_reached(sentry_init, capture_envelopes, monkeypatch):
    """New spans are dropped if a bucket reaches MAX_BEFORE_DROP spans."""
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_DROP", 2)
    # set the time-based flush limit to something huge so that we're not hitting
    # it
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 1000)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

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

    # XXX client reports?


def test_weight_based_flushing(sentry_init, capture_envelopes, monkeypatch):
    monkeypatch.setattr(SpanBatcher, "MAX_BYTES_BEFORE_FLUSH", 1)
    # set the time-based flush limit to something huge so that we're not hitting it
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 1000)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span 1"):
        pass

    time.sleep(0.1)

    with sentry_sdk.traces.start_span(name="span 2"):
        pass

    time.sleep(0.5)

    assert len(envelopes) == 2

    assert len(envelopes[0].items[0].payload.json["items"]) == 1
    assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"

    assert len(envelopes[1].items[0].payload.json["items"]) == 1
    assert envelopes[1].items[0].payload.json["items"][0]["name"] == "span 2"


def test_quiet_buckets_flush_eventually(sentry_init, capture_envelopes, monkeypatch):
    """Even if a bucket doesn't trigger any size limits, it'll get flushed eventually."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 0.1)
    # these are purposefully high so as to never trigger in this test scenario
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 10000000)
    monkeypatch.setattr(SpanBatcher, "MAX_BYTES_BEFORE_FLUSH", 10000000)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span 1"):
        pass

    time.sleep(0.5)

    assert len(envelopes) == 1

    assert len(envelopes[0].items[0].payload.json["items"]) == 1
    assert envelopes[0].items[0].payload.json["items"][0]["name"] == "span 1"


def test_quiet_buckets_flushed_with_busy_neighbors(
    sentry_init, capture_envelopes, monkeypatch
):
    """When there's a combination of busy and quiet buckets, the quiet ones get flushed eventually."""
    monkeypatch.setattr(SpanBatcher, "FLUSH_WAIT_TIME", 0.1)
    monkeypatch.setattr(SpanBatcher, "MAX_BEFORE_FLUSH", 5)

    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span 1") as span1:
        trace_id1 = span1.trace_id

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span 2") as span2:
        trace_id2 = span2.trace_id

    for i in range(3, 10):
        with sentry_sdk.traces.start_span(name=f"span {i}"):
            pass

    time.sleep(0.3)

    assert len(envelopes) >= 2

    # we don't care how exactly the spans are distributed over envelopes
    # (since both the time and length based limits are effective, it might not
    # be stable); just check whether the spans are all there and that each
    # envelope only contains spans from one trace
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
