import asyncio
import os
import re
import sys
import time
from unittest import mock
from typing import Any

import pytest

import sentry_sdk
from sentry_sdk.profiler.continuous_profiler import get_profiler_id
from sentry_sdk.traces import NoOpStreamedSpan, SpanStatus, StreamedSpan


minimum_python_38 = pytest.mark.skipif(
    sys.version_info < (3, 8), reason="Asyncio tests need Python >= 3.8"
)


def envelopes_to_spans(envelopes):
    res: "list[dict[str, Any]]" = []
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

    with sentry_sdk.traces.start_span(name="segment") as segment:
        assert segment._is_segment() is True
        with sentry_sdk.traces.start_span(name="child") as child:
            assert child._is_segment() is False
            assert child._segment == segment

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
    child = sentry_sdk.traces.start_span(name="child")
    assert child._segment == segment
    child.end()
    segment.end()

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


def test_span_sampled_when_created(sentry_init, capture_envelopes):
    # Test that if a span is created without the context manager, it is sampled
    # at start_span() time

    def traces_sampler(sampling_context):
        assert "delayed_attribute" not in sampling_context["span_context"]["attributes"]
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    segment = sentry_sdk.traces.start_span(name="segment")
    segment.set_attribute("delayed_attribute", 12)
    segment.end()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (segment,) = spans

    assert segment["name"] == "segment"
    assert segment["attributes"]["delayed_attribute"] == 12


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
        assert "attributes" in sampling_context["span_context"]
        assert "my_attribute" in sampling_context["span_context"]["attributes"]
        assert (
            sampling_context["span_context"]["attributes"]["my_attribute"] == "my_value"
        )
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


def test_sampling_context(sentry_init, capture_envelopes):
    received_trace_id = None

    def traces_sampler(sampling_context):
        nonlocal received_trace_id

        assert "trace_id" in sampling_context["span_context"]
        received_trace_id = sampling_context["span_context"]["trace_id"]

        assert "parent_span_id" in sampling_context["span_context"]
        assert sampling_context["span_context"]["parent_span_id"] is None

        assert "parent_sampled" in sampling_context["span_context"]
        assert sampling_context["span_context"]["parent_sampled"] is None

        assert "attributes" in sampling_context["span_context"]

        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        trace_id = span._trace_id

    assert received_trace_id == trace_id

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1


def test_custom_sampling_context(sentry_init):
    class MyClass: ...

    my_class = MyClass()

    def traces_sampler(sampling_context):
        assert "class" in sampling_context
        assert "string" in sampling_context
        assert sampling_context["class"] == my_class
        assert sampling_context["string"] == "my string"
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    sentry_sdk.get_current_scope().set_custom_sampling_context(
        {
            "class": my_class,
            "string": "my string",
        }
    )

    with sentry_sdk.traces.start_span(name="span"):
        ...


def test_custom_sampling_context_update_to_context_value_persists(sentry_init):
    def traces_sampler(sampling_context):
        if sampling_context["span_context"]["attributes"]["first"] is True:
            assert sampling_context["custom_value"] == 1
        else:
            assert sampling_context["custom_value"] == 2
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    sentry_sdk.traces.new_trace()

    sentry_sdk.get_current_scope().set_custom_sampling_context({"custom_value": 1})

    with sentry_sdk.traces.start_span(name="span", attributes={"first": True}):
        ...

    sentry_sdk.traces.new_trace()

    sentry_sdk.get_current_scope().set_custom_sampling_context({"custom_value": 2})

    with sentry_sdk.traces.start_span(name="span", attributes={"first": False}):
        ...


def test_span_attributes(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(
        name="segment", attributes={"attribute1": "value"}
    ) as span:
        assert span.get_attributes()["attribute1"] == "value"
        span.set_attribute("attribute2", 47)
        span.remove_attribute("attribute1")
        span.set_attributes({"attribute3": 4.5, "attribute4": False})
        assert "attribute1" not in span.get_attributes()
        attributes = span.get_attributes()
        assert attributes["attribute2"] == 47
        assert attributes["attribute3"] == 4.5
        assert attributes["attribute4"] is False

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "segment"
    assert "attribute1" not in span["attributes"]
    assert span["attributes"]["attribute2"] == 47
    assert span["attributes"]["attribute3"] == 4.5
    assert span["attributes"]["attribute4"] is False


def test_span_attributes_serialize_early(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    class Class:
        pass

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_attributes(
            {
                # arrays of different types will be serialized
                "attribute1": [123, "text"],
                # so will custom class instances
                "attribute2": Class(),
            }
        )
        attributes = span.get_attributes()
        assert isinstance(attributes["attribute1"], str)
        assert attributes["attribute1"] == "[123, 'text']"
        assert isinstance(attributes["attribute2"], str)
        assert "Class" in attributes["attribute2"]

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["attributes"]["attribute1"] == "[123, 'text']"
    assert "Class" in span["attributes"]["attribute2"]


def test_traces_sampler_drops_span(sentry_init, capture_envelopes):
    def traces_sampler(sampling_context):
        assert "attributes" in sampling_context["span_context"]
        assert "drop" in sampling_context["span_context"]["attributes"]

        if sampling_context["span_context"]["attributes"]["drop"] is True:
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
    span_name_in_traces_sampler = None

    def traces_sampler(sampling_context):
        nonlocal traces_sampler_called, span_name_in_traces_sampler
        traces_sampler_called += 1
        span_name_in_traces_sampler = sampling_context["span_context"]["name"]
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
    assert span_name_in_traces_sampler == segment.name


def test_start_inactive_span(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment") as segment:
        with sentry_sdk.traces.start_span(name="child1", active=False):
            with sentry_sdk.traces.start_span(name="child2"):
                # Should have segment as parent since child1 is inactive
                pass

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 3
    child2, child1, segment = spans

    assert segment["is_segment"] is True
    assert segment["name"] == "segment"
    assert segment["attributes"]["sentry.segment.name"] == "segment"

    assert child1["is_segment"] is False
    assert child1["name"] == "child1"
    assert child1["attributes"]["sentry.segment.name"] == "segment"
    assert child1["parent_span_id"] == segment["span_id"]
    assert child1["trace_id"] == segment["trace_id"]

    assert child2["is_segment"] is False
    assert child2["name"] == "child2"
    assert child2["attributes"]["sentry.segment.name"] == "segment"
    assert child2["parent_span_id"] == segment["span_id"]
    assert child2["trace_id"] == segment["trace_id"]


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

    assert segment1["trace_id"] == segment2["trace_id"]


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


def test_continue_trace_sampled(sentry_init, capture_envelopes):
    sentry_init(
        # parent sampling decision takes precedence over traces_sample_rate
        traces_sample_rate=0.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    trace_id = "0af7651916cd43dd8448eb211c80319c"
    parent_span_id = "b7ad6b7169203331"
    sample_rand = "0.222222"
    sampled = "1"

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"{trace_id}-{parent_span_id}-{sampled}",
            "baggage": f"sentry-trace_id={trace_id},sentry-sample_rate=0.5,sentry-sample_rand={sample_rand}",
        }
    )

    with sentry_sdk.traces.start_span(name="segment") as span:
        ...

    assert span.sampled is True
    assert span.trace_id == trace_id
    assert span._parent_span_id == parent_span_id
    assert span._sample_rand == float(sample_rand)

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (segment,) = spans

    assert segment["is_segment"] is True
    assert segment["parent_span_id"] == parent_span_id
    assert segment["trace_id"] == trace_id


def test_continue_trace_unsampled(sentry_init, capture_envelopes):
    sentry_init(
        # parent sampling decision takes precedence over traces_sample_rate
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    trace_id = "0af7651916cd43dd8448eb211c80319c"
    parent_span_id = "b7ad6b7169203331"
    sample_rand = "0.999999"
    sampled = "0"

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"{trace_id}-{parent_span_id}-{sampled}",
            "baggage": f"sentry-trace_id={trace_id},sentry-sample_rate=0.5,sentry-sample_rand={sample_rand}",
        }
    )

    with sentry_sdk.traces.start_span(name="segment") as span:
        ...

    assert span.sampled is False
    assert span.name == ""
    assert span.trace_id == "00000000000000000000000000000000"
    assert span.span_id == "0000000000000000"

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 0


def test_unsampled_spans_produce_client_report(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=0.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child1"):
            pass
        with sentry_sdk.traces.start_span(name="child2"):
            pass

    sentry_sdk.get_client().flush()

    spans = envelopes_to_spans(envelopes)
    assert not spans

    assert record_lost_event_calls == [
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
        ("sample_rate", "span", None, 1),
    ]


def test_no_client_reports_if_tracing_is_off(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=None,
        _experiments={"trace_lifecycle": "stream"},
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment"):
        with sentry_sdk.traces.start_span(name="child1"):
            pass
        with sentry_sdk.traces.start_span(name="child2"):
            pass

    sentry_sdk.get_client().flush()

    spans = envelopes_to_spans(envelopes)
    assert not spans
    assert not record_lost_event_calls


def test_continue_trace_no_sample_rand(sentry_init, capture_envelopes):
    sentry_init(
        # parent sampling decision takes precedence over traces_sample_rate
        traces_sample_rate=0.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    trace_id = "0af7651916cd43dd8448eb211c80319c"
    parent_span_id = "b7ad6b7169203331"
    sampled = "1"

    sentry_sdk.traces.continue_trace(
        {
            "sentry-trace": f"{trace_id}-{parent_span_id}-{sampled}",
            "baggage": f"sentry-trace_id={trace_id},sentry-sample_rate=0.5",
        }
    )

    with sentry_sdk.traces.start_span(name="segment") as span:
        ...

    assert span.sampled is True
    assert span.trace_id == trace_id
    assert span._parent_span_id == parent_span_id
    assert isinstance(span._sample_rand, float)

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (segment,) = spans

    assert segment["is_segment"] is True
    assert segment["parent_span_id"] == parent_span_id
    assert segment["trace_id"] == trace_id


def test_outgoing_traceparent_and_baggage(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span") as span:
        assert span.sampled is True

        trace_id = span.trace_id
        span_id = span.span_id

        traceparent = sentry_sdk.get_traceparent()
        assert traceparent == f"{trace_id}-{span_id}-1"

        baggage = sentry_sdk.get_baggage()
        baggage_items = dict(tuple(item.split("=")) for item in baggage.split(","))
        assert "sentry-trace_id" in baggage_items
        assert baggage_items["sentry-trace_id"] == trace_id
        assert "sentry-sampled" in baggage_items
        assert baggage_items["sentry-sampled"] == "true"


def test_outgoing_traceparent_and_baggage_when_noop_span_is_active(
    sentry_init, capture_envelopes
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    sentry_sdk.traces.new_trace()

    propagation_context = (
        sentry_sdk.get_current_scope().get_active_propagation_context()
    )
    propagation_trace_id = propagation_context.trace_id
    propagation_span_id = propagation_context.span_id

    with sentry_sdk.traces.start_span(name="ignored") as span:
        assert span.sampled is False

        noop_trace_id = span.trace_id
        noop_span_id = span.span_id

        traceparent = sentry_sdk.get_traceparent()
        assert traceparent != f"{noop_trace_id}-{noop_span_id}"
        assert traceparent == f"{propagation_trace_id}-{propagation_span_id}"

        baggage = sentry_sdk.get_baggage()
        baggage_items = dict(tuple(item.split("=")) for item in baggage.split(","))
        assert "sentry-trace_id" in baggage_items
        assert baggage_items["sentry-trace_id"] != noop_trace_id
        assert baggage_items["sentry-trace_id"] == propagation_trace_id


def test_trace_decorator(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace
    def traced_function(): ...

    traced_function()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert (
        span["name"]
        == "test_span_streaming.test_trace_decorator.<locals>.traced_function"
    )
    assert span["status"] == "ok"


def test_trace_decorator_arguments(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace(name="traced", attributes={"traced.attribute": 123})
    def traced_function(): ...

    traced_function()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "traced"
    assert span["attributes"]["traced.attribute"] == 123
    assert span["status"] == "ok"


def test_trace_decorator_inactive(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace(name="outer", active=False)
    def traced_function():
        with sentry_sdk.traces.start_span(name="inner"):
            ...

    traced_function()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    (span1, span2) = spans

    assert span1["name"] == "inner"
    assert span1["parent_span_id"] != span2["span_id"]

    assert span2["name"] == "outer"


@minimum_python_38
def test_trace_decorator_async(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace
    async def traced_function(): ...

    asyncio.run(traced_function())

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert (
        span["name"]
        == "test_span_streaming.test_trace_decorator_async.<locals>.traced_function"
    )
    assert span["status"] == "ok"


@minimum_python_38
def test_trace_decorator_async_arguments(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace(name="traced", attributes={"traced.attribute": 123})
    async def traced_function(): ...

    asyncio.run(traced_function())

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "traced"
    assert span["attributes"]["traced.attribute"] == 123
    assert span["status"] == "ok"


@minimum_python_38
def test_trace_decorator_async_inactive(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    @sentry_sdk.traces.trace(name="outer", active=False)
    async def traced_function():
        with sentry_sdk.traces.start_span(name="inner"):
            ...

    asyncio.run(traced_function())

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    (span1, span2) = spans

    assert span1["name"] == "inner"
    assert span1["parent_span_id"] != span2["span_id"]

    assert span2["name"] == "outer"


def test_set_span_status(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        span.status = SpanStatus.ERROR

    with sentry_sdk.traces.start_span(name="span") as span:
        span.status = "error"

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    (span1, span2) = spans

    assert span1["status"] == "error"
    assert span2["status"] == "error"


def test_set_span_status_on_error(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with pytest.raises(ValueError):
        with sentry_sdk.traces.start_span(name="span") as span:
            raise ValueError("oh no!")

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["status"] == "error"


def test_set_span_status_on_ignored_span(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream", "ignore_spans": ["ignored"]},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="ignored") as span:
        span.status = "error"

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 0


@pytest.mark.parametrize(
    ("ignore_spans", "name", "attributes", "ignored"),
    [
        # no regexes
        ([], "/health", {}, False),
        ([{}], "/health", {}, False),
        (["/health"], "/health", {}, True),
        (["/health"], "/health", {"custom": "custom"}, True),
        ([{"name": "/health"}], "/health", {}, True),
        ([{"name": "/health"}], "/health", {"custom": "custom"}, True),
        ([{"attributes": {"custom": "custom"}}], "/health", {"custom": "custom"}, True),
        ([{"attributes": {"custom": "custom"}}], "/health", {}, False),
        (
            [{"name": "/nothealth", "attributes": {"custom": "custom"}}],
            "/health",
            {"custom": "custom"},
            False,
        ),
        (
            [{"name": "/health", "attributes": {"custom": "notcustom"}}],
            "/health",
            {"custom": "custom"},
            False,
        ),
        (
            [{"name": "/health", "attributes": {"custom": "custom"}}],
            "/health",
            {"custom": "custom"},
            True,
        ),
        # test cases with regexes
        ([re.compile("/hea.*")], "/health", {}, True),
        ([re.compile("/hea.*")], "/health", {"custom": "custom"}, True),
        ([{"name": re.compile("/hea.*")}], "/health", {}, True),
        ([{"name": re.compile("/hea.*")}], "/health", {"custom": "custom"}, True),
        (
            [{"attributes": {"custom": re.compile("c.*")}}],
            "/health",
            {"custom": "custom"},
            True,
        ),
        ([{"attributes": {"custom": re.compile("c.*")}}], "/health", {}, False),
        (
            [
                {
                    "name": re.compile("/nothea.*"),
                    "attributes": {"custom": re.compile("c.*")},
                }
            ],
            "/health",
            {"custom": "custom"},
            False,
        ),
        (
            [
                {
                    "name": re.compile("/hea.*"),
                    "attributes": {"custom": re.compile("notc.*")},
                }
            ],
            "/health",
            {"custom": "custom"},
            False,
        ),
        (
            [
                {
                    "name": re.compile("/hea.*"),
                    "attributes": {"custom": re.compile("c.*")},
                }
            ],
            "/health",
            {"custom": "custom"},
            True,
        ),
        (
            [{"attributes": {"listattr": re.compile(r"\[.*\]")}}],
            "/a",
            {"listattr": [1, 2, 3]},
            False,
        ),
    ],
)
def test_ignore_spans(
    sentry_init, capture_envelopes, ignore_spans, name, attributes, ignored
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ignore_spans,
        },
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name=name, attributes=attributes) as span:
        if ignored:
            assert span.sampled is False
            assert isinstance(span, NoOpStreamedSpan)
        else:
            assert span.sampled is True
            assert isinstance(span, StreamedSpan)

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    if ignored:
        assert len(spans) == 0
    else:
        assert len(spans) == 1
        (span,) = spans
        assert span["name"] == name


def test_ignore_spans_basic(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()
    lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="ignored") as ignored_span:
        assert ignored_span.sampled is False

    with sentry_sdk.traces.start_span(name="not ignored") as span:
        assert span.sampled is True

    sentry_sdk.get_client().flush()

    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans
    assert span["name"] == "not ignored"
    assert span["parent_span_id"] is None

    assert len(lost_event_calls) == 1
    assert lost_event_calls[0] == ("ignored", "span", None, 1)


def test_ignore_spans_ignored_segment_drops_whole_tree(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    # Ignored segments should drop the whole span tree.
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()
    lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="ignored") as ignored_span:
        assert ignored_span.sampled is False
        assert isinstance(ignored_span, NoOpStreamedSpan)

        with sentry_sdk.traces.start_span(name="not ignored") as span1:
            assert span1.sampled is False
            assert isinstance(span1, NoOpStreamedSpan)

            with sentry_sdk.traces.start_span(name="not ignored") as span2:
                assert span2.sampled is False
                assert isinstance(span2, NoOpStreamedSpan)

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 0

    assert len(lost_event_calls) == 3
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("ignored", "span", None, 1)


def test_ignore_spans_ignored_segment_drops_whole_tree_explicit_parent_span(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    # Ignored segments should drop the whole span tree.
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()
    lost_event_calls = capture_record_lost_event_calls()

    ignored_span = sentry_sdk.traces.start_span(name="ignored")
    assert isinstance(ignored_span, NoOpStreamedSpan)
    assert ignored_span.sampled is False

    span1 = sentry_sdk.traces.start_span(name="not ignored 1", parent_span=ignored_span)
    assert isinstance(span1, NoOpStreamedSpan)
    assert span1.sampled is False

    span2 = sentry_sdk.traces.start_span(name="not ignored 2", parent_span=ignored_span)
    assert isinstance(span2, NoOpStreamedSpan)
    assert span2.sampled is False

    span1.end()
    span2.end()
    ignored_span.end()

    sentry_sdk.get_client().flush()

    spans = envelopes_to_spans(events)

    assert len(spans) == 0

    assert len(lost_event_calls) == 3
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("ignored", "span", None, 1)


def test_ignore_spans_set_ignored_child_span_as_parent(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    # Ignored non-segment spans should NOT drop the whole subtree under them.
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()
    lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="segment") as segment:
        assert segment.sampled is True

        with sentry_sdk.traces.start_span(name="ignored") as ignored_span1:
            assert ignored_span1.sampled is False

            with sentry_sdk.traces.start_span(name="ignored") as ignored_span2:
                assert ignored_span2.sampled is False

                with sentry_sdk.traces.start_span(name="child") as span:
                    assert span.sampled is True
                    assert span._parent_span_id == segment.span_id

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    (child, segment) = spans
    assert segment["name"] == "segment"
    assert child["name"] == "child"
    assert child["parent_span_id"] == segment["span_id"]  # reparented to segment

    assert len(lost_event_calls) == 2
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("ignored", "span", None, 1)


def test_ignore_spans_set_ignored_child_span_as_parent_explicit_parent_span(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    # Ignored non-segment spans should NOT drop the whole subtree under them.
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()
    lost_event_calls = capture_record_lost_event_calls()

    segment = sentry_sdk.traces.start_span(name="segment")
    assert not isinstance(segment, NoOpStreamedSpan)
    assert segment.sampled is True
    assert segment._parent_span_id is None

    ignored_span1 = sentry_sdk.traces.start_span(name="ignored", parent_span=segment)
    assert isinstance(ignored_span1, NoOpStreamedSpan)
    assert ignored_span1.sampled is False

    ignored_span2 = sentry_sdk.traces.start_span(
        name="ignored", parent_span=ignored_span1
    )
    assert isinstance(ignored_span2, NoOpStreamedSpan)
    assert ignored_span2.sampled is False

    span = sentry_sdk.traces.start_span(name="child", parent_span=ignored_span2)
    assert not isinstance(span, NoOpStreamedSpan)
    assert span.sampled is True
    assert span._parent_span_id == segment.span_id
    span.end()

    ignored_span2.end()
    ignored_span1.end()
    segment.end()

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 2
    (child, segment) = spans
    assert segment["name"] == "segment"
    assert child["name"] == "child"
    assert child["parent_span_id"] == segment["span_id"]  # reparented to segment

    assert len(lost_event_calls) == 2
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("ignored", "span", None, 1)


def test_ignore_spans_reparenting(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "ignore_spans": ["ignored"],
        },
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment") as span1:
        assert span1.sampled is True
        assert span1._parent_span_id is None

        with sentry_sdk.traces.start_span(name="ignored") as span2:
            assert span2.sampled is False

            with sentry_sdk.traces.start_span(name="child 1") as span3:
                assert span3.sampled is True
                assert span3._parent_span_id == span1.span_id

                with sentry_sdk.traces.start_span(name="ignored") as span4:
                    assert span4.sampled is False

                    with sentry_sdk.traces.start_span(name="child 2") as span5:
                        assert span5.sampled is True
                        assert span5._parent_span_id == span3.span_id

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 3
    (span5, span3, span1) = spans
    assert span1["name"] == "segment"
    assert span3["name"] == "child 1"
    assert span5["name"] == "child 2"
    assert span3["parent_span_id"] == span1["span_id"]
    assert span5["parent_span_id"] == span3["span_id"]


def test_ignored_spans_produce_client_report(
    sentry_init, capture_envelopes, capture_record_lost_event_calls
):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream", "ignore_spans": ["ignored"]},
    )

    envelopes = capture_envelopes()
    record_lost_event_calls = capture_record_lost_event_calls()

    with sentry_sdk.traces.start_span(name="ignored"):
        with sentry_sdk.traces.start_span(name="span1"):
            pass
        with sentry_sdk.traces.start_span(name="span2"):
            pass

    sentry_sdk.get_client().flush()

    spans = envelopes_to_spans(envelopes)
    assert not spans

    # All three spans will be ignored since the segment is ignored
    assert record_lost_event_calls == [
        ("ignored", "span", None, 1),
        ("ignored", "span", None, 1),
        ("ignored", "span", None, 1),
    ]


@mock.patch("sentry_sdk.profiler.continuous_profiler.DEFAULT_SAMPLING_FREQUENCY", 21)
def test_segment_span_has_profiler_id(
    sentry_init, capture_envelopes, teardown_profiling
):
    sentry_init(
        traces_sample_rate=1.0,
        profile_lifecycle="trace",
        profiler_mode="thread",
        profile_session_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "continuous_profiling_auto_start": True,
        },
    )
    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="profiled segment"):
        time.sleep(0.1)

    sentry_sdk.get_client().flush()
    time.sleep(0.3)  # wait for profiler to flush

    spans = envelopes_to_spans(envelopes)
    assert len(spans) == 1
    assert "sentry.profiler_id" in spans[0]["attributes"]

    profile_chunks = [
        item
        for envelope in envelopes
        for item in envelope.items
        if item.type == "profile_chunk"
    ]
    assert len(profile_chunks) > 0


def test_segment_span_no_profiler_id_when_unsampled(
    sentry_init, capture_envelopes, teardown_profiling
):
    sentry_init(
        traces_sample_rate=1.0,
        profile_lifecycle="trace",
        profiler_mode="thread",
        profile_session_sample_rate=0.0,
        _experiments={
            "trace_lifecycle": "stream",
            "continuous_profiling_auto_start": True,
        },
    )
    envelopes = capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment"):
        time.sleep(0.05)

    sentry_sdk.get_client().flush()
    time.sleep(0.2)

    spans = envelopes_to_spans(envelopes)
    assert len(spans) == 1
    assert "sentry.profiler_id" not in spans[0]["attributes"]

    profile_chunks = [
        item
        for envelope in envelopes
        for item in envelope.items
        if item.type == "profile_chunk"
    ]
    assert len(profile_chunks) == 0


@mock.patch("sentry_sdk.profiler.continuous_profiler.DEFAULT_SAMPLING_FREQUENCY", 21)
def test_profile_stops_when_segment_ends(
    sentry_init, capture_envelopes, teardown_profiling
):
    sentry_init(
        traces_sample_rate=1.0,
        profile_lifecycle="trace",
        profiler_mode="thread",
        profile_session_sample_rate=1.0,
        _experiments={
            "trace_lifecycle": "stream",
            "continuous_profiling_auto_start": True,
        },
    )
    capture_envelopes()

    with sentry_sdk.traces.start_span(name="segment") as span:
        time.sleep(0.1)
        assert span._continuous_profile is not None
        assert span._continuous_profile.active is True

    assert span._continuous_profile.active is False

    time.sleep(0.3)
    assert get_profiler_id() is None, "profiler should have stopped"


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
                    "thread.id": {"value": mock.ANY, "type": "string"},
                    "thread.name": {"value": "MainThread", "type": "string"},
                    "sentry.segment.id": {"value": mock.ANY, "type": "string"},
                    "sentry.segment.name": {"value": "test", "type": "string"},
                    "sentry.sdk.name": {"value": "sentry.python", "type": "string"},
                    "sentry.sdk.version": {"value": mock.ANY, "type": "string"},
                    "server.address": {"value": "test-server", "type": "string"},
                    "sentry.environment": {"value": "production", "type": "string"},
                    "sentry.release": {"value": "1.0.0", "type": "string"},
                    "sentry.origin": {"value": "manual", "type": "string"},
                },
            }
        ]
    }


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
        _experiments={"trace_lifecycle": "stream"},
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
