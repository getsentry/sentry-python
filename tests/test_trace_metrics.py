import json
import sys
from typing import List, Any, Mapping
import pytest

import sentry_sdk
import sentry_sdk.trace_metrics
from sentry_sdk import get_client
from sentry_sdk.envelope import Envelope
from sentry_sdk.types import TraceMetric

minimum_python_37 = pytest.mark.skipif(
    sys.version_info < (3, 7), reason="Asyncio tests need Python >= 3.7"
)


def envelopes_to_trace_metrics(envelopes):
    # type: (List[Envelope]) -> List[TraceMetric]
    res = []  # type: List[TraceMetric]
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
                            k: v["value"] for (k, v) in metric_json["attributes"].items()
                        },
                    }  # type: TraceMetric
                    res.append(metric)
    return res


@minimum_python_37
def test_trace_metrics_disabled_by_default(sentry_init, capture_envelopes):
    sentry_init()

    envelopes = capture_envelopes()

    sentry_sdk.trace_metrics.count("test.counter", 1)
    sentry_sdk.trace_metrics.gauge("test.gauge", 42)
    sentry_sdk.trace_metrics.distribution("test.distribution", 200)

    assert len(envelopes) == 0


@minimum_python_37
def test_trace_metrics_basics(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enableMetrics": True})
    envelopes = capture_envelopes()

    sentry_sdk.trace_metrics.count("test.counter", 1)
    sentry_sdk.trace_metrics.gauge("test.gauge", 42, unit="millisecond")
    sentry_sdk.trace_metrics.distribution("test.distribution", 200, unit="second")

    get_client().flush()
    metrics = envelopes_to_trace_metrics(envelopes)
    
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


@minimum_python_37
def test_trace_metrics_experimental_option(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enableMetrics": True})
    envelopes = capture_envelopes()

    sentry_sdk.trace_metrics.count("test.counter", 5)

    get_client().flush()

    metrics = envelopes_to_trace_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["name"] == "test.counter"
    assert metrics[0]["type"] == "counter"
    assert metrics[0]["value"] == 5.0


@minimum_python_37
def test_trace_metrics_with_attributes(sentry_init, capture_envelopes):
    sentry_init(
        _experiments={"enableMetrics": True}, release="1.0.0", environment="test"
    )
    envelopes = capture_envelopes()

    sentry_sdk.trace_metrics.count(
        "test.counter", 1, attributes={"endpoint": "/api/test", "status": "success"}
    )

    get_client().flush()

    metrics = envelopes_to_trace_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["endpoint"] == "/api/test"
    assert metrics[0]["attributes"]["status"] == "success"
    assert metrics[0]["attributes"]["sentry.release"] == "1.0.0"
    assert metrics[0]["attributes"]["sentry.environment"] == "test"


@minimum_python_37
def test_trace_metrics_with_user(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enableMetrics": True})
    envelopes = capture_envelopes()

    sentry_sdk.set_user(
        {"id": "user-123", "email": "test@example.com", "username": "testuser"}
    )
    sentry_sdk.trace_metrics.count("test.user.counter", 1)

    get_client().flush()

    metrics = envelopes_to_trace_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["user.id"] == "user-123"
    assert metrics[0]["attributes"]["user.email"] == "test@example.com"
    assert metrics[0]["attributes"]["user.name"] == "testuser"


@minimum_python_37
def test_trace_metrics_with_span(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enableMetrics": True}, traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(op="test", name="test-span") as span:
        sentry_sdk.trace_metrics.count("test.span.counter", 1)

    get_client().flush()

    metrics = envelopes_to_trace_metrics(envelopes)
    assert len(metrics) == 1

    assert metrics[0]["trace_id"] is not None
    assert metrics[0]["trace_id"] != "00000000-0000-0000-0000-000000000000"
    assert metrics[0]["span_id"] is not None


@minimum_python_37
def test_trace_metrics_before_send(sentry_init, capture_envelopes):
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
            "enableMetrics": True,
            "before_send_metric": _before_metric,
        },
    )
    envelopes = capture_envelopes()

    sentry_sdk.trace_metrics.count("test.skip", 1)
    sentry_sdk.trace_metrics.count("test.keep", 1)

    get_client().flush()

    metrics = envelopes_to_trace_metrics(envelopes)
    assert len(metrics) == 1
    assert metrics[0]["name"] == "test.keep"
    assert before_metric_called
