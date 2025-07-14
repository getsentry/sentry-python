import pytest
from opentelemetry import trace

import sentry_sdk
from sentry_sdk.consts import SPANSTATUS, VERSION
from tests.conftest import ApproxDict


tracer = trace.get_tracer(__name__)


@pytest.fixture(autouse=True)
def sentry_init_potel(sentry_init):
    sentry_init(traces_sample_rate=1.0)


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
    assert trace_context["origin"] == "manual"

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

    assert span["description"] == "db"
    assert span["origin"] == "manual"
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

    assert db_span["description"] == "db"
    assert redis_span["description"] == "redis"
    assert http_span["description"] == "http"

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
    assert trace_context["origin"] == "manual"
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

    assert span["description"] == "db"
    assert span["origin"] == "manual"
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

    assert db_span["description"] == "db"
    assert redis_span["description"] == "redis"
    assert http_span["description"] == "http"

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

    assert db_span["description"] == "db"
    assert redis_span["description"] == "redis"
    assert http_span["description"] == "http"

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

    assert payload["contexts"]["trace"]["data"] == ApproxDict({"foo": "bar", "baz": 42})
    assert payload["spans"][0]["data"] == ApproxDict({"abc": 99, "def": "moo"})


def test_span_data_started_with_sentry(capture_envelopes):
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(op="http", description="request") as request_span:
        request_span.set_attribute("foo", "bar")
        with sentry_sdk.start_span(op="db", description="statement") as db_span:
            db_span.set_attribute("baz", 42)

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["contexts"]["trace"]["data"] == ApproxDict(
        {
            "foo": "bar",
            "sentry.origin": "manual",
            "sentry.description": "request",
            "sentry.op": "http",
        }
    )
    assert payload["spans"][0]["data"] == ApproxDict(
        {
            "baz": 42,
            "sentry.origin": "manual",
            "sentry.description": "statement",
            "sentry.op": "db",
        }
    )


def test_transaction_tags_started_with_otel(capture_envelopes):
    envelopes = capture_envelopes()

    sentry_sdk.set_tag("tag.global", 99)
    with tracer.start_as_current_span("request"):
        sentry_sdk.set_tag("tag.inner", "foo")

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["tags"] == {"tag.global": "99", "tag.inner": "foo"}


def test_transaction_tags_started_with_sentry(capture_envelopes):
    envelopes = capture_envelopes()

    sentry_sdk.set_tag("tag.global", 99)
    with sentry_sdk.start_span(description="request"):
        sentry_sdk.set_tag("tag.inner", "foo")

    (envelope,) = envelopes
    (item,) = envelope.items
    payload = item.payload.json

    assert payload["tags"] == {"tag.global": "99", "tag.inner": "foo"}


def test_multiple_transaction_tags_isolation_scope_started_with_otel(capture_envelopes):
    envelopes = capture_envelopes()

    sentry_sdk.set_tag("tag.global", 99)
    with sentry_sdk.isolation_scope():
        with tracer.start_as_current_span("request a"):
            sentry_sdk.set_tag("tag.inner.a", "a")
    with sentry_sdk.isolation_scope():
        with tracer.start_as_current_span("request b"):
            sentry_sdk.set_tag("tag.inner.b", "b")

    (payload_a, payload_b) = [envelope.items[0].payload.json for envelope in envelopes]

    assert payload_a["tags"] == {"tag.global": "99", "tag.inner.a": "a"}
    assert payload_b["tags"] == {"tag.global": "99", "tag.inner.b": "b"}


def test_multiple_transaction_tags_isolation_scope_started_with_sentry(
    capture_envelopes,
):
    envelopes = capture_envelopes()

    sentry_sdk.set_tag("tag.global", 99)
    with sentry_sdk.isolation_scope():
        with sentry_sdk.start_span(description="request a"):
            sentry_sdk.set_tag("tag.inner.a", "a")
    with sentry_sdk.isolation_scope():
        with sentry_sdk.start_span(description="request b"):
            sentry_sdk.set_tag("tag.inner.b", "b")

    (payload_a, payload_b) = [envelope.items[0].payload.json for envelope in envelopes]

    assert payload_a["tags"] == {"tag.global": "99", "tag.inner.a": "a"}
    assert payload_b["tags"] == {"tag.global": "99", "tag.inner.b": "b"}


def test_potel_span_root_span_references():
    with sentry_sdk.start_span(description="request") as request_span:
        assert request_span.is_root_span
        assert request_span.root_span == request_span
        with sentry_sdk.start_span(description="db") as db_span:
            assert not db_span.is_root_span
            assert db_span.root_span == request_span
            with sentry_sdk.start_span(description="redis") as redis_span:
                assert not redis_span.is_root_span
                assert redis_span.root_span == request_span
        with sentry_sdk.start_span(description="http") as http_span:
            assert not http_span.is_root_span
            assert http_span.root_span == request_span


@pytest.mark.parametrize(
    "status_in,status_out",
    [
        (None, None),
        ("", SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.OK, SPANSTATUS.OK),
        (SPANSTATUS.ABORTED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.ALREADY_EXISTS, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.CANCELLED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.DATA_LOSS, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.DEADLINE_EXCEEDED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.FAILED_PRECONDITION, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.INTERNAL_ERROR, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.INVALID_ARGUMENT, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.NOT_FOUND, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.OUT_OF_RANGE, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.PERMISSION_DENIED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.RESOURCE_EXHAUSTED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.UNAUTHENTICATED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.UNAVAILABLE, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.UNIMPLEMENTED, SPANSTATUS.UNKNOWN_ERROR),
        (SPANSTATUS.UNKNOWN_ERROR, SPANSTATUS.UNKNOWN_ERROR),
    ],
)
def test_potel_span_status(status_in, status_out):
    span = sentry_sdk.start_span(name="test")
    if status_in is not None:
        span.set_status(status_in)

    assert span.status == status_out


def test_otel_resource(sentry_init):
    sentry_init()

    tracer_provider = trace.get_tracer_provider()
    resource_attrs = tracer_provider.resource.attributes
    assert resource_attrs["service.name"] == "sentry-python"
    assert resource_attrs["service.namespace"] == "sentry"
    assert resource_attrs["service.version"] == VERSION
