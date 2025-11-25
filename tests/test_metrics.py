import json
import sys
from typing import List, Any, Mapping
import pytest

import sentry_sdk
from sentry_sdk import get_client
from sentry_sdk.envelope import Envelope
from sentry_sdk.types import Metric
from sentry_sdk.consts import SPANDATA, VERSION


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
    sentry_init(release="1.0.0", environment="test", server_name="test-server")
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

    assert metrics[0]["attributes"][SPANDATA.SERVER_ADDRESS] == "test-server"
    assert metrics[0]["attributes"]["sentry.sdk.name"].startswith("sentry.python")
    assert metrics[0]["attributes"]["sentry.sdk.version"] == VERSION


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
