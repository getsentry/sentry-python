import asyncio
import time
import sys
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.traces import SegmentSource, SpanStatus

minimum_python_38 = pytest.mark.skipif(
    sys.version_info < (3, 8), reason="Asyncio tests need Python >= 3.8"
)


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

    with sentry_sdk.traces.start_span(name="segment") as segment:
        assert segment.is_segment() is True
        with sentry_sdk.traces.start_span(name="child") as child:
            assert child.is_segment() is False
            assert child.segment == segment

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
    assert child.segment == segment
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


def test_span_sampled_at_start(sentry_init, capture_envelopes):
    # Test that if a span is created without the context manager, it is sampled
    # at .start() time rather than creation time

    def traces_sampler(sampling_context):
        assert "delayed_attribute" in sampling_context["attributes"]
        assert sampling_context["attributes"]["delayed_attribute"] == 12
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    segment = sentry_sdk.traces.start_span(name="segment")
    segment.set_attribute("delayed_attribute", 12)
    segment.start()
    segment.finish()

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


def test_sampling_context(sentry_init, capture_envelopes):
    def traces_sampler(sampling_context):
        assert "transaction_context" in sampling_context
        assert "trace_id" in sampling_context["transaction_context"]
        assert "span_id" in sampling_context["transaction_context"]
        assert "parent_span_id" in sampling_context["transaction_context"]
        assert "parent_sampled" in sampling_context
        assert "attributes" in sampling_context
        return 1.0

    sentry_init(
        traces_sampler=traces_sampler,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span"):
        ...

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1


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


def test_traces_sampler_receives_custom_sampling_context(sentry_init):
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
    assert span.parent_span_id == parent_span_id
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
    assert span.trace_id == trace_id
    assert span.parent_span_id == parent_span_id
    assert span._sample_rand == float(sample_rand)

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 0


def test_outgoing_traceparent_and_baggage(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    sentry_sdk.traces.new_trace()

    with sentry_sdk.traces.start_span(name="span") as span:
        assert span.sampled is True

        trace_id = span._trace_id
        span_id = span._span_id

        traceparent = sentry_sdk.get_traceparent()
        assert traceparent == f"{trace_id}-{span_id}-1"

        baggage = sentry_sdk.get_baggage()
        baggage_items = dict(tuple(item.split("=")) for item in baggage.split(","))
        assert "sentry-trace_id" in baggage_items
        assert baggage_items["sentry-trace_id"] == trace_id
        assert "sentry-sampled" in baggage_items
        assert baggage_items["sentry-sampled"] == "true"


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


def test_set_span_status(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_status(SpanStatus.ERROR)

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_status("error")

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


def test_set_span_op(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_op("function")
        assert span.get_attributes()["sentry.op"] == "function"

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "span"
    assert span["attributes"]["sentry.op"] == "function"


def test_set_span_source(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_source(SegmentSource.TASK)
        assert span.get_attributes()["sentry.span.source"] == SegmentSource.TASK.value

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "span"
    assert span["attributes"]["sentry.span.source"] == SegmentSource.TASK.value


def test_set_span_origin(sentry_init, capture_envelopes):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream"},
    )

    events = capture_envelopes()

    with sentry_sdk.traces.start_span(name="span") as span:
        span.set_origin("redis")
        assert span.get_attributes()["sentry.origin"] == "redis"

    sentry_sdk.get_client().flush()
    spans = envelopes_to_spans(events)

    assert len(spans) == 1
    (span,) = spans

    assert span["name"] == "span"
    assert span["attributes"]["sentry.origin"] == "redis"


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
