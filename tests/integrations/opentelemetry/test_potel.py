import pytest

from opentelemetry import trace

import sentry_sdk


tracer = trace.get_tracer(__name__)


@pytest.fixture(autouse=True)
def sentry_init_potel(sentry_init):
    sentry_init(
        traces_sample_rate=1.0,
        _experiments={"otel_powered_performance": True},
    )


def test_root_span_transaction_payload_started_with_otel_only(capture_envelopes):
    envelopes = capture_envelopes()

    with tracer.start_as_current_span("request"):
        pass

    (envelope,) = envelopes
    # TODO-neel-potel DSC header
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["type"] == "transaction"
    assert payload["transaction"] == "request"
    assert payload["transaction_info"] == {"source": "custom"}
    assert payload["timestamp"] is not None
    assert payload["start_timestamp"] is not None

    contexts = payload["contexts"]
    assert "runtime" in contexts
    assert "otel" in contexts
    assert "resource" in contexts["otel"]

    trace_context = contexts["trace"]
    assert "trace_id" in trace_context
    assert "span_id" in trace_context
    assert trace_context["origin"] == "auto.otel"
    assert trace_context["op"] == "request"
    assert trace_context["status"] == "ok"

    assert payload["spans"] == []


def test_child_span_payload_started_with_otel_only(capture_envelopes):
    envelopes = capture_envelopes()

    with tracer.start_as_current_span("request"):
        with tracer.start_as_current_span("db"):
            pass

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json
    (span,) = payload["spans"]

    assert span["op"] == "db"
    assert span["description"] == "db"
    assert span["origin"] == "auto.otel"
    assert span["status"] == "ok"
    assert span["span_id"] is not None
    assert span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert span["timestamp"] is not None
    assert span["start_timestamp"] is not None


def test_children_span_nesting_started_with_otel_only(capture_envelopes):
    envelopes = capture_envelopes()

    with tracer.start_as_current_span("request"):
        with tracer.start_as_current_span("db"):
            with tracer.start_as_current_span("redis"):
                pass
        with tracer.start_as_current_span("http"):
            pass

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json
    (db_span, http_span, redis_span) = payload["spans"]

    assert db_span["op"] == "db"
    assert redis_span["op"] == "redis"
    assert http_span["op"] == "http"

    assert db_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert redis_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert http_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]

    assert db_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert http_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert redis_span["parent_span_id"] == db_span["span_id"]


def test_root_span_transaction_payload_started_with_sentry_only(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request"):
        pass

    (envelope,) = envelopes
    # TODO-neel-potel DSC header
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["type"] == "transaction"
    assert payload["transaction"] == "request"
    assert payload["transaction_info"] == {"source": "custom"}
    assert payload["timestamp"] is not None
    assert payload["start_timestamp"] is not None

    contexts = payload["contexts"]
    assert "runtime" in contexts
    assert "otel" in contexts
    assert "resource" in contexts["otel"]

    trace_context = contexts["trace"]
    assert "trace_id" in trace_context
    assert "span_id" in trace_context
    assert trace_context["origin"] == "auto.otel"
    assert trace_context["op"] == "request"
    assert trace_context["status"] == "ok"

    assert payload["spans"] == []


def test_child_span_payload_started_with_sentry_only(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request"):
        with sentry_sdk.start_span(description="db"):
            pass

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json
    (span,) = payload["spans"]

    assert span["op"] == "db"
    assert span["description"] == "db"
    assert span["origin"] == "auto.otel"
    assert span["status"] == "ok"
    assert span["span_id"] is not None
    assert span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert span["timestamp"] is not None
    assert span["start_timestamp"] is not None


def test_children_span_nesting_started_with_sentry_only(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request"):
        with sentry_sdk.start_span(description="db"):
            with sentry_sdk.start_span(description="redis"):
                pass
        with sentry_sdk.start_span(description="http"):
            pass

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json
    (db_span, http_span, redis_span) = payload["spans"]

    assert db_span["op"] == "db"
    assert redis_span["op"] == "redis"
    assert http_span["op"] == "http"

    assert db_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert redis_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert http_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]

    assert db_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert http_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert redis_span["parent_span_id"] == db_span["span_id"]


def test_children_span_nesting_mixed(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(description="request"):
        with tracer.start_as_current_span("db"):
            with sentry_sdk.start_span(description="redis"):
                pass
        with tracer.start_as_current_span("http"):
            pass

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json
    (db_span, http_span, redis_span) = payload["spans"]

    assert db_span["op"] == "db"
    assert redis_span["op"] == "redis"
    assert http_span["op"] == "http"

    assert db_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert redis_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]
    assert http_span["trace_id"] == payload["contexts"]["trace"]["trace_id"]

    assert db_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert http_span["parent_span_id"] == payload["contexts"]["trace"]["span_id"]
    assert redis_span["parent_span_id"] == db_span["span_id"]


def test_span_attributes_in_data_started_with_otel(capture_envelopes):
    envelopes = capture_envelopes()

    with tracer.start_as_current_span("request") as request_span:
        request_span.set_attributes({"foo": "bar", "baz": 42})
        with tracer.start_as_current_span("db") as db_span:
            db_span.set_attributes({"abc": 99, "def": "moo"})

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["contexts"]["trace"]["data"] == {"foo": "bar", "baz": 42}
    assert payload["spans"][0]["data"] == {"abc": 99, "def": "moo"}


def test_span_data_started_with_sentry(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(op="http", description="request") as request_span:
        request_span.set_data("foo", "bar")
        with sentry_sdk.start_span(op="db", description="statement") as db_span:
            db_span.set_data("baz", 42)

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["contexts"]["trace"]["data"] == {
        "foo": "bar",
        "sentry.origin": "manual",
        "sentry.description": "request",
        "sentry.op": "http",
    }
    assert payload["spans"][0]["data"] == {
        "baz": 42,
        "sentry.origin": "manual",
        "sentry.description": "statement",
        "sentry.op": "db",
    }
