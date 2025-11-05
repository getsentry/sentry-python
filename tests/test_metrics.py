import json
import sys
from typing import List, Any, Mapping
from unittest import mock
import pytest

import sentry_sdk
from sentry_sdk import get_client
from sentry_sdk.envelope import Envelope
from sentry_sdk.types import Metric


def envelopes_to_metrics(envelopes):
    # type: (List[Envelope]) -> List[Metric]
    res = []  # type: List[Metric]
    for envelope in envelopes:
        for item in envelope.items:
            if item.type == "trace_metric":
                for metric_json in item.payload.json["items"]:
                    metric = {
                        "timestamp": metric_json["timestamp"],
                        "trace_id": metric_json["trace_id"],
                        "span_id": metric_json.get("span_id"),
                        "name": metric_json["name"],
                        "type": metric_json["type"],
                        "value": metric_json["value"],
                        "unit": metric_json.get("unit"),
                        "attributes": {
                            k: v["value"]
                            for (k, v) in metric_json["attributes"].items()
                        },
                    }  # type: Metric
                    res.append(metric)
    return res


def test_metrics_disabled(sentry_init, capture_envelopes):
    sentry_init(enable_metrics=False)

    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)
    sentry_sdk.metrics.gauge("test.gauge", 42)
    sentry_sdk.metrics.distribution("test.distribution", 200)

    assert len(envelopes) == 0


def test_metrics_basics(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)
    sentry_sdk.metrics.gauge("test.gauge", 42, unit="millisecond")
    sentry_sdk.metrics.distribution("test.distribution", 200, unit="second")

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 3

    assert metrics[0]["name"] == "test.counter"
    assert metrics[0]["type"] == "counter"
    assert metrics[0]["value"] == 1.0
    assert metrics[0]["unit"] is None
    assert "sentry.sdk.name" in metrics[0]["attributes"]
    assert "sentry.sdk.version" in metrics[0]["attributes"]

    assert metrics[1]["name"] == "test.gauge"
    assert metrics[1]["type"] == "gauge"
    assert metrics[1]["value"] == 42.0
    assert metrics[1]["unit"] == "millisecond"

    assert metrics[2]["name"] == "test.distribution"
    assert metrics[2]["type"] == "distribution"
    assert metrics[2]["value"] == 200.0
    assert metrics[2]["unit"] == "second"


def test_metrics_experimental_option(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 5)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["name"] == "test.counter"
    assert metrics[0]["type"] == "counter"
    assert metrics[0]["value"] == 5.0


def test_metrics_with_attributes(sentry_init, capture_envelopes):
    sentry_init(release="1.0.0", environment="test")
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count(
        "test.counter", 1, attributes={"endpoint": "/api/test", "status": "success"}
    )

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["endpoint"] == "/api/test"
    assert metrics[0]["attributes"]["status"] == "success"
    assert metrics[0]["attributes"]["sentry.release"] == "1.0.0"
    assert metrics[0]["attributes"]["sentry.environment"] == "test"


def test_metrics_with_user(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_sdk.set_user(
        {"id": "user-123", "email": "test@example.com", "username": "testuser"}
    )
    sentry_sdk.metrics.count("test.user.counter", 1)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["user.id"] == "user-123"
    assert metrics[0]["attributes"]["user.email"] == "test@example.com"
    assert metrics[0]["attributes"]["user.name"] == "testuser"


def test_metrics_with_span(sentry_init, capture_envelopes):
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(op="test", name="test-span") as transaction:
        sentry_sdk.metrics.count("test.span.counter", 1)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["trace_id"] is not None
    assert metrics[0]["trace_id"] == transaction.trace_id
    assert metrics[0]["span_id"] == transaction.span_id


def test_metrics_tracing_without_performance(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    with sentry_sdk.isolation_scope() as isolation_scope:
        sentry_sdk.metrics.count("test.span.counter", 1)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1

    propagation_context = isolation_scope._propagation_context
    assert propagation_context is not None
    assert metrics[0]["trace_id"] == propagation_context.trace_id
    assert metrics[0]["span_id"] == propagation_context.span_id


def test_metrics_before_send(sentry_init, capture_envelopes):
    before_metric_called = False

    def _before_metric(record, hint):
        nonlocal before_metric_called

        assert set(record.keys()) == {
            "timestamp",
            "trace_id",
            "span_id",
            "name",
            "type",
            "value",
            "unit",
            "attributes",
        }

        if record["name"] == "test.skip":
            return None

        before_metric_called = True
        return record

    sentry_init(
        before_send_metric=_before_metric,
    )
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.skip", 1)
    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1
    assert metrics[0]["name"] == "test.keep"
    assert before_metric_called


def test_metrics_experimental_before_send(sentry_init, capture_envelopes):
    before_metric_called = False

    def _before_metric(record, hint):
        nonlocal before_metric_called

        assert set(record.keys()) == {
            "timestamp",
            "trace_id",
            "span_id",
            "name",
            "type",
            "value",
            "unit",
            "attributes",
        }

        if record["name"] == "test.skip":
            return None

        before_metric_called = True
        return record

    sentry_init(
        _experiments={
            "before_send_metric": _before_metric,
        },
    )
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.skip", 1)
    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    assert len(metrics) == 1
    assert metrics[0]["name"] == "test.keep"
    assert before_metric_called


def test_batcher_drops_metrics(sentry_init, monkeypatch):
    sentry_init()
    client = sentry_sdk.get_client()

    def no_op_flush():
        pass

    monkeypatch.setattr(client.metrics_batcher, "_flush", no_op_flush)

    lost_event_calls = []

    def record_lost_event(reason, data_category, quantity):
        lost_event_calls.append((reason, data_category, quantity))

    monkeypatch.setattr(client.metrics_batcher, "_record_lost_func", record_lost_event)

    for i in range(10_005):  # 5 metrics over the hard limit
        sentry_sdk.metrics.count("test.counter", 1)

    assert len(lost_event_calls) == 5
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("queue_overflow", "trace_metric", 1)


def test_metrics_sample_rate_basic(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1, sample_rate=0.5)
    sentry_sdk.metrics.gauge("test.gauge", 42, sample_rate=0.8)
    sentry_sdk.metrics.distribution("test.distribution", 200, sample_rate=1.0)

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 3

    assert metrics[0]["name"] == "test.counter"
    # No sentry.client_sample_rate when there's no trace context
    assert "sentry.client_sample_rate" not in metrics[0]["attributes"]

    assert metrics[1]["name"] == "test.gauge"
    # No sentry.client_sample_rate when there's no trace context
    assert "sentry.client_sample_rate" not in metrics[1]["attributes"]

    assert metrics[2]["name"] == "test.distribution"
    assert "sentry.client_sample_rate" not in metrics[2]["attributes"]


def test_metrics_sample_rate_normalization(sentry_init, capture_envelopes, monkeypatch):
    sentry_init()
    envelopes = capture_envelopes()
    client = sentry_sdk.get_client()

    lost_event_calls = []

    def record_lost_event(reason, data_category, quantity):
        lost_event_calls.append((reason, data_category, quantity))

    monkeypatch.setattr(client.transport, "record_lost_event", record_lost_event)

    sentry_sdk.metrics.count("test.counter1", 1, sample_rate=0.0)  # <= 0
    sentry_sdk.metrics.count("test.counter2", 1, sample_rate=-0.5)  # < 0
    sentry_sdk.metrics.count("test.counter3", 1, sample_rate=0.5)  # > 0 but < 1.0
    sentry_sdk.metrics.count("test.counter4", 1, sample_rate=1.0)  # = 1.0
    sentry_sdk.metrics.count("test.counter4", 1, sample_rate=1.5)  # > 1.0

    client.flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 2

    # No sentry.client_sample_rate when there's no trace context
    assert "sentry.client_sample_rate" not in metrics[0]["attributes"]
    assert (
        "sentry.client_sample_rate" not in metrics[1]["attributes"]
    )  # 1.0 does not need a sample rate, it's implied to be 1.0

    assert len(lost_event_calls) == 3
    assert lost_event_calls[0] == ("invalid_sample_rate", "trace_metric", 1)
    assert lost_event_calls[1] == ("invalid_sample_rate", "trace_metric", 1)
    assert lost_event_calls[2] == ("invalid_sample_rate", "trace_metric", 1)


def test_metrics_no_sample_rate(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 1

    assert "sentry.client_sample_rate" not in metrics[0]["attributes"]


def test_metrics_sample_rate_no_trace_context(sentry_init, capture_envelopes):
    """Test sentry.client_sample_rate not set when there's no trace context."""
    sentry_init()
    envelopes = capture_envelopes()

    # Send metrics with sample_rate but without any active transaction/span
    sentry_sdk.metrics.count("test.counter", 1, sample_rate=0.5)
    sentry_sdk.metrics.gauge("test.gauge", 42, sample_rate=0.8)

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 2

    # When there's no trace context, no sampling is performed,
    # so sentry.client_sample_rate should not be set
    assert "sentry.client_sample_rate" not in metrics[0]["attributes"]
    assert "sentry.client_sample_rate" not in metrics[1]["attributes"]


def test_metrics_sample_rate_with_trace_context(
    sentry_init, capture_envelopes, monkeypatch
):
    """Test sentry.client_sample_rate is set when there's a trace context."""
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    # Mock the random sampling to ensure all metrics are included
    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange",
        return_value=0,  # Always sample (0 < any positive sample_rate)
    ):
        # Send metrics with sample_rate within an active transaction
        with sentry_sdk.start_transaction() as _:
            sentry_sdk.metrics.count("test.counter", 1, sample_rate=0.5)
            sentry_sdk.metrics.gauge("test.gauge", 42, sample_rate=0.8)
            sentry_sdk.metrics.distribution("test.distribution", 200, sample_rate=1.0)

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    assert len(metrics) == 3

    # When there's a trace context and sample_rate < 1.0, set attribute
    assert metrics[0]["attributes"]["sentry.client_sample_rate"] == 0.5
    assert metrics[1]["attributes"]["sentry.client_sample_rate"] == 0.8
    # sample_rate=1.0 doesn't need the attribute
    assert "sentry.client_sample_rate" not in metrics[2]["attributes"]


@pytest.mark.parametrize("sample_rand", (0.0, 0.25, 0.5, 0.75))
@pytest.mark.parametrize("sample_rate", (0.0, 0.25, 0.5, 0.75, 1.0))
def test_metrics_sampling_decision(
    sentry_init, capture_envelopes, sample_rate, sample_rand, monkeypatch
):
    sentry_init(traces_sample_rate=1.0)
    envelopes = capture_envelopes()
    client = sentry_sdk.get_client()

    lost_event_calls = []

    def record_lost_event(reason, data_category, quantity):
        lost_event_calls.append((reason, data_category, quantity))

    monkeypatch.setattr(client.transport, "record_lost_event", record_lost_event)

    with mock.patch(
        "sentry_sdk.tracing_utils.Random.randrange",
        return_value=int(sample_rand * 1000000),
    ):
        with sentry_sdk.start_transaction() as _:
            sentry_sdk.metrics.count("test.counter", 1, sample_rate=sample_rate)

    get_client().flush()
    metrics = envelopes_to_metrics(envelopes)

    should_be_sampled = sample_rand < sample_rate and sample_rate > 0.0
    assert len(metrics) == int(should_be_sampled)

    if sample_rate <= 0.0:
        assert len(lost_event_calls) == 1
        assert lost_event_calls[0] == ("invalid_sample_rate", "trace_metric", 1)
    elif not should_be_sampled:
        assert len(lost_event_calls) == 1
        assert lost_event_calls[0] == ("sample_rate", "trace_metric", 1)
    else:
        assert len(lost_event_calls) == 0
