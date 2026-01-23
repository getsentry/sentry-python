import time
from unittest import mock

import sentry_sdk


def envelopes_to_spans(envelopes):
    res = []  # type: List[Metric]
    for envelope in envelopes:
        for item in envelope.items:
            if item.type == "span":
                for span_json in item.payload.json["items"]:
                    span = {
                        "start_timestamp": span_json["start_timestamp"],
                        "end_timestamp": span_json.get("end_timestamp"),
                        "trace_id": span_json["trace_id"],
                        "span_id": span_json["span_id"],
                        "name": span_json["name"],
                        "status": span_json["status"],
                        "is_segment": span_json["is_segment"],
                        "parent_span_id": span_json.get("parent_span_id"),
                        "attributes": {
                            k: v["value"] for (k, v) in span_json["attributes"].items()
                        },
                    }
                    res.append(span)
    return res


def test_start_span(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child"):
            ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    child, segment = spans

    assert segment["name"] == "segment"
    assert segment["attributes"]["sentry.segment.name"] == "segment"
    assert child["name"] == "child"
    assert child["attributes"]["sentry.segment.name"] == "segment"

    assert segment["is_segment"] is True
    assert segment["parent_span_id"] is None
    assert child["is_segment"] is False
    assert child["parent_span_id"] == segment["span_id"]
    assert child["trace_id"] == segment["trace_id"]

    assert segment["start_timestamp"] is not None
    assert child["start_timestamp"] is not None
    assert segment["end_timestamp"] is not None
    assert child["end_timestamp"] is not None

    assert child["status"] == "ok"
    assert segment["status"] == "ok"


def test_start_span_no_context_manager(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    segment = sentry_sdk.traces.start_span(name="segment")
    segment.start()
    child = sentry_sdk.traces.start_span(name="child")
    child.start()
    child.finish()
    segment.finish()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    child, segment = spans

    assert segment["name"] == "segment"
    assert segment["attributes"]["sentry.segment.name"] == "segment"
    assert child["name"] == "child"
    assert child["attributes"]["sentry.segment.name"] == "segment"

    assert segment["is_segment"] is True
    assert child["is_segment"] is False
    assert child["parent_span_id"] == segment["span_id"]
    assert child["trace_id"] == segment["trace_id"]

    assert segment["start_timestamp"] is not None
    assert child["start_timestamp"] is not None
    assert segment["end_timestamp"] is not None
    assert child["end_timestamp"] is not None

    assert child["status"] == "ok"
    assert segment["status"] == "ok"


def test_start_span_attributes(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(
        name="segment", attributes={"my_attribute": "my_value"}
    ):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "segment"
    assert span["attributes"]["my_attribute"] == "my_value"


def test_start_span_attributes_in_traces_sampler(sentry_init, capture_envelopes):
    def traces_sampler(sampling_context):
        assert "attributes" in sampling_context
        assert "my_attribute" in sampling_context["attributes"]
        assert sampling_context["attributes"]["my_attribute"] == "my_value"
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(
        name="segment", attributes={"my_attribute": "my_value"}
    ):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "segment"
    assert span["attributes"]["my_attribute"] == "my_value"


def test_traces_sampler_drops_span(sentry_init, capture_envelopes):
    def traces_sampler(sampling_context):
        assert "attributes" in sampling_context
        assert "drop" in sampling_context["attributes"]

        if sampling_context["attributes"]["drop"] is True:
            return 0.0

        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="dropped", attributes={"drop": True}):
        ...
    with sentry_sdk.traces.start_span(name="retained", attributes={"drop": False}):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "retained"
    assert span["attributes"]["drop"] is False


def test_traces_sampler_called_once_per_segment(sentry_init):
    traces_sampler_called = 0
    span_id_in_traces_sampler = None

    def traces_sampler(sampling_context):
        nonlocal traces_sampler_called, span_id_in_traces_sampler
        traces_sampler_called += 1
        span_id_in_traces_sampler = sampling_context["transaction_context"]["span_id"]
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    with sentry_sdk.traces.start_span(name="segment") as segment:
        with sentry_sdk.traces.start_span(name="child1"):
            ...
        with sentry_sdk.traces.start_span(name="child2"):
            with sentry_sdk.traces.start_span(name="child3"):
                ...

    assert traces_sampler_called == 1
    assert span_id_in_traces_sampler == segment.span_id


def test_start_span_override_parent(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment") as segment:
        with sentry_sdk.traces.start_span(name="child1"):
            with sentry_sdk.traces.start_span(name="child2", parent_span=segment):
                pass

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 3
    child2, child1, segment = spans

    assert segment["name"] == "segment"
    assert segment["attributes"]["sentry.segment.name"] == "segment"

    assert child1["name"] == "child1"
    assert child1["attributes"]["sentry.segment.name"] == "segment"

    assert child2["name"] == "child2"
    assert child2["attributes"]["sentry.segment.name"] == "segment"

    assert segment["is_segment"] is True

    assert child1["is_segment"] is False
    assert child1["parent_span_id"] == segment["span_id"]
    assert child1["trace_id"] == segment["trace_id"]

    assert child2["is_segment"] is False
    assert child2["parent_span_id"] == segment["span_id"]
    assert child2["trace_id"] == segment["trace_id"]


def test_sibling_segments(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment1"):
        ...

    with sentry_sdk.traces.start_span(name="segment2"):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    segment1, segment2 = spans

    assert segment1["name"] == "segment1"
    assert segment1["attributes"]["sentry.segment.name"] == "segment1"
    assert segment1["is_segment"] is True
    assert segment1["parent_span_id"] is None

    assert segment2["name"] == "segment2"
    assert segment2["attributes"]["sentry.segment.name"] == "segment2"
    assert segment2["is_segment"] is True
    assert segment2["parent_span_id"] is None

    assert segment1["trace_id"] == segment1["trace_id"]


def test_sibling_segments_new_trace(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment1"):
        ...

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="segment2"):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    segment1, segment2 = spans

    assert segment1["name"] == "segment1"
    assert segment1["attributes"]["sentry.segment.name"] == "segment1"
    assert segment1["is_segment"] is True
    assert segment1["parent_span_id"] is None

    assert segment2["name"] == "segment2"
    assert segment2["attributes"]["sentry.segment.name"] == "segment2"
    assert segment2["is_segment"] is True
    assert segment2["parent_span_id"] is None

    assert segment1["trace_id"] != segment2["trace_id"]


def test_transport_format(sentry_init, capture_envelopes):
    sentry_init(
        server_name="test-server",
        release="1.0.0",
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
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
        "items": [
            {
                "trace_id": mock.ANY,
                "span_id": mock.ANY,
                "name": "test",
                "status": "ok",
                "is_segment": True,
                "start_timestamp": mock.ANY,
                "end_timestamp": mock.ANY,
                "attributes": {
                    "sentry.span.source": {"value": "custom", "type": "string"},
                    "thread.id": {"value": mock.ANY, "type": "string"},
                    "thread.name": {"value": "MainThread", "type": "string"},
                    "sentry.segment.name": {"value": "test", "type": "string"},
                    "sentry.sdk.name": {"value": "sentry.python", "type": "string"},
                    "sentry.sdk.version": {"value": mock.ANY, "type": "string"},
                    "server.address": {"value": "test-server", "type": "string"},
                    "sentry.environment": {"value": "production", "type": "string"},
                    "sentry.release": {"value": "1.0.0", "type": "string"},
                },
            }
        ]
    }
