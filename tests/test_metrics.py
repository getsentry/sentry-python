import os
import sys
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import get_client
from sentry_sdk.consts import SPANDATA, VERSION


def test_metrics_disabled(sentry_init, capture_envelopes):
    sentry_init(enable_metrics=False)

    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)
    sentry_sdk.metrics.gauge("test.gauge", 42)
    sentry_sdk.metrics.distribution("test.distribution", 200)

    assert len(envelopes) == 0


def test_metrics_basics(sentry_init, capture_items):
    sentry_init()
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count("test.counter", 1)
    sentry_sdk.metrics.gauge("test.gauge", 42, unit="millisecond")
    sentry_sdk.metrics.distribution("test.distribution", 200, unit="second")

    get_client().flush()
    metrics = [item.payload for item in items]

    assert len(metrics) == 3

    assert metrics[0]["name"] == "test.counter"
    assert metrics[0]["type"] == "counter"
    assert metrics[0]["value"] == 1.0
    assert "unit" not in metrics[0]
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


def test_metrics_experimental_option(sentry_init, capture_items):
    sentry_init()
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count("test.counter", 5)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert metrics[0]["name"] == "test.counter"
    assert metrics[0]["type"] == "counter"
    assert metrics[0]["value"] == 5.0


def test_metrics_with_attributes(sentry_init, capture_items):
    sentry_init(release="1.0.0", environment="test", server_name="test-server")
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count(
        "test.counter", 1, attributes={"endpoint": "/api/test", "status": "success"}
    )

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["endpoint"] == "/api/test"
    assert metrics[0]["attributes"]["status"] == "success"
    assert metrics[0]["attributes"]["sentry.release"] == "1.0.0"
    assert metrics[0]["attributes"]["sentry.environment"] == "test"

    assert metrics[0]["attributes"][SPANDATA.SERVER_ADDRESS] == "test-server"
    assert metrics[0]["attributes"]["sentry.sdk.name"].startswith("sentry.python")
    assert metrics[0]["attributes"]["sentry.sdk.version"] == VERSION


def test_metrics_with_user(sentry_init, capture_items):
    sentry_init(send_default_pii=True)
    items = capture_items("trace_metric")

    sentry_sdk.set_user(
        {"id": "user-123", "email": "test@example.com", "username": "testuser"}
    )
    sentry_sdk.metrics.count("test.user.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert metrics[0]["attributes"]["user.id"] == "user-123"
    assert metrics[0]["attributes"]["user.email"] == "test@example.com"
    assert metrics[0]["attributes"]["user.name"] == "testuser"


def test_metrics_no_user_if_pii_off(sentry_init, capture_items):
    sentry_init(send_default_pii=False)
    items = capture_items("trace_metric")

    sentry_sdk.set_user(
        {"id": "user-123", "email": "test@example.com", "username": "testuser"}
    )
    sentry_sdk.metrics.count("test.user.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert "user.id" not in metrics[0]["attributes"]
    assert "user.email" not in metrics[0]["attributes"]
    assert "user.name" not in metrics[0]["attributes"]


def test_metrics_with_span(sentry_init, capture_items):
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("trace_metric")

    with sentry_sdk.start_transaction(op="test", name="test-span") as transaction:
        sentry_sdk.metrics.count("test.span.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert metrics[0]["trace_id"] is not None
    assert metrics[0]["trace_id"] == transaction.trace_id
    assert metrics[0]["span_id"] == transaction.span_id


def test_metrics_tracing_without_performance(sentry_init, capture_items):
    sentry_init()
    items = capture_items("trace_metric")

    with sentry_sdk.isolation_scope() as isolation_scope:
        sentry_sdk.metrics.count("test.span.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    propagation_context = isolation_scope._propagation_context
    assert propagation_context is not None
    assert metrics[0]["trace_id"] == propagation_context.trace_id
    # Per the metrics spec, span_id is only attached when a span is
    # active when the metric is emitted. The propagation context's
    # synthesized span_id must not be used as a fallback.
    assert "span_id" not in metrics[0]


def test_metrics_before_send(sentry_init, capture_items):
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
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count("test.skip", 1)
    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1
    assert metrics[0]["name"] == "test.keep"
    assert before_metric_called


def test_metrics_experimental_before_send(sentry_init, capture_items):
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
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count("test.skip", 1)
    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1
    assert metrics[0]["name"] == "test.keep"
    assert before_metric_called


def test_transport_format(sentry_init, capture_envelopes):
    sentry_init(server_name="test-server", release="1.0.0")

    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)

    sentry_sdk.get_client().flush()

    assert len(envelopes) == 1
    assert len(envelopes[0].items) == 1
    item = envelopes[0].items[0]

    assert item.type == "trace_metric"
    assert item.headers == {
        "type": "trace_metric",
        "item_count": 1,
        "content_type": "application/vnd.sentry.items.trace-metric+json",
    }
    assert item.payload.json == {
        "items": [
            {
                "name": "test.counter",
                "type": "counter",
                "value": 1,
                "timestamp": mock.ANY,
                "trace_id": mock.ANY,
                "attributes": {
                    "sentry.environment": {
                        "type": "string",
                        "value": "production",
                    },
                    "sentry.release": {
                        "type": "string",
                        "value": "1.0.0",
                    },
                    "sentry.sdk.name": {
                        "type": "string",
                        "value": mock.ANY,
                    },
                    "sentry.sdk.version": {
                        "type": "string",
                        "value": VERSION,
                    },
                    "server.address": {
                        "type": "string",
                        "value": "test-server",
                    },
                },
            }
        ]
    }


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


def test_metric_gets_attributes_from_scopes(sentry_init, capture_items):
    sentry_init()

    items = capture_items("trace_metric")

    global_scope = sentry_sdk.get_global_scope()
    global_scope.set_attribute("global.attribute", "value")

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("current.attribute", "value")
        sentry_sdk.metrics.count("test", 1)

    sentry_sdk.metrics.count("test", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    (metric1, metric2) = metrics

    assert metric1["attributes"]["global.attribute"] == "value"
    assert metric1["attributes"]["current.attribute"] == "value"

    assert metric2["attributes"]["global.attribute"] == "value"
    assert "current.attribute" not in metric2["attributes"]


def test_metric_attributes_override_scope_attributes(sentry_init, capture_items):
    sentry_init()

    items = capture_items("trace_metric")

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("durable.attribute", "value1")
        scope.set_attribute("temp.attribute", "value1")
        sentry_sdk.metrics.count("test", 1, attributes={"temp.attribute": "value2"})

    get_client().flush()

    metrics = [item.payload for item in items]
    (metric,) = metrics

    assert metric["attributes"]["durable.attribute"] == "value1"
    assert metric["attributes"]["temp.attribute"] == "value2"


def test_log_array_attributes(sentry_init, capture_envelopes):
    """Test homogeneous list and tuple attributes, and fallback for inhomogeneous collections."""

    sentry_init()

    envelopes = capture_envelopes()

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("string_list.attribute", ["value1", "value2"])
        scope.set_attribute("int_tuple.attribute", (3, 2, 1, 4))
        scope.set_attribute("inhomogeneous_tuple.attribute", (3, 2.0, 1, 4))  # type: ignore[arg-type]

        sentry_sdk.metrics.count(
            "test",
            1,
            attributes={
                "float_list.attribute": [3.0, 3.5, 4.2],
                "bool_tuple.attribute": (False, False, True),
                "inhomogeneous_list.attribute": [3.2, True, None],
            },
        )

    get_client().flush()

    assert len(envelopes) == 1
    assert len(envelopes[0].items) == 1
    item = envelopes[0].items[0]
    serialized_attributes = item.payload.json["items"][0]["attributes"]

    assert serialized_attributes["string_list.attribute"] == {
        "value": ["value1", "value2"],
        "type": "array",
    }
    assert serialized_attributes["int_tuple.attribute"] == {
        "value": [3, 2, 1, 4],
        "type": "array",
    }
    assert serialized_attributes["inhomogeneous_tuple.attribute"] == {
        "value": "(3, 2.0, 1, 4)",
        "type": "string",
    }

    assert serialized_attributes["float_list.attribute"] == {
        "value": [3.0, 3.5, 4.2],
        "type": "array",
    }
    assert serialized_attributes["bool_tuple.attribute"] == {
        "value": [False, False, True],
        "type": "array",
    }
    assert serialized_attributes["inhomogeneous_list.attribute"] == {
        "value": "[3.2, True, None]",
        "type": "string",
    }


def test_attributes_preserialized_in_before_send(sentry_init, capture_items):
    """We don't surface user-held references to objects in attributes."""

    def before_send_metric(metric, _):
        assert isinstance(metric["attributes"]["instance"], str)
        assert isinstance(metric["attributes"]["dictionary"], str)
        assert isinstance(metric["attributes"]["inhomogeneous_list"], str)
        assert isinstance(metric["attributes"]["inhomogeneous_tuple"], str)

        return metric

    sentry_init(before_send_metric=before_send_metric)

    items = capture_items("trace_metric")

    class Cat:
        pass

    instance = Cat()
    dictionary = {"color": "tortoiseshell"}

    sentry_sdk.metrics.count(
        "test.counter",
        1,
        attributes={
            "instance": instance,
            "dictionary": dictionary,
            "inhomogeneous_list": [3.2, True, None],
            "inhomogeneous_tuple": (3, 2.0, 1, 4),
        },
    )

    get_client().flush()

    metrics = [item.payload for item in items]
    (metric,) = metrics

    assert isinstance(metric["attributes"]["instance"], str)
    assert isinstance(metric["attributes"]["dictionary"], str)


def test_array_attributes_deep_copied_in_before_send(sentry_init, capture_envelopes):
    """We don't surface user-held references to objects in attributes."""

    strings = ["value1", "value2"]
    ints = (3, 2, 1, 4)

    def before_send_metric(metric, _):
        assert metric["attributes"]["string_list"] is not strings
        assert metric["attributes"]["int_tuple"] is not ints

        return metric

    sentry_init(before_send_metric=before_send_metric)

    sentry_sdk.metrics.count(
        "test.counter",
        1,
        attributes={
            "string_list": strings,
            "int_tuple": ints,
        },
    )

    get_client().flush()


@pytest.mark.skipif(
    sys.platform == "win32"
    or not hasattr(os, "fork")
    or not hasattr(os, "register_at_fork"),
    reason="requires POSIX fork and os.register_at_fork (Python 3.7+)",
)
def test_metrics_batcher_lock_reset_in_child_after_fork(sentry_init):
    """Regression test for the MetricsBatcher fork-deadlock fix.

    If os.fork() runs while another thread holds MetricsBatcher._lock,
    the child inherits the lock locked. The holding thread does not
    exist in the child, so the lock can never be released and
    _ensure_thread deadlocks forever. The after-fork hook must replace
    the lock with a fresh one in the child and reset
    _flusher / _flusher_pid / _buffer / _active / _flush_event.
    """
    sentry_init()
    batcher = sentry_sdk.get_client().metrics_batcher
    assert batcher is not None

    original_lock = batcher._lock
    original_lock.acquire()

    batcher._buffer.append(object())
    batcher._active.flag = True
    batcher._flush_event.set()
    batcher._running = False

    pid = os.fork()
    if pid == 0:
        replaced = batcher._lock is not original_lock
        unheld = batcher._lock.acquire(blocking=False)

        flusher_reset = batcher._flusher is None and batcher._flusher_pid is None
        buffer_reset = len(batcher._buffer) == 0
        active_reset = not getattr(batcher._active, "flag", False)
        event_reset = not batcher._flush_event.is_set()
        running_reset = batcher._running is True

        os._exit(
            0
            if replaced
            and unheld
            and flusher_reset
            and buffer_reset
            and active_reset
            and event_reset
            and running_reset
            else 1
        )

    original_lock.release()
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
