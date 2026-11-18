import os
import sys
import threading
import time
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import get_client
from sentry_sdk._lock_free_metrics_batcher import LockFreeMetricsBatcher
from sentry_sdk.consts import SPANDATA, VERSION


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
    sentry_init(data_collection={"user_info": True})
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


def test_metrics_no_user_if_user_info_off(sentry_init, capture_items):
    sentry_init(data_collection={"user_info": False})
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

    with sentry_sdk.start_span(name="test-span") as segment:
        sentry_sdk.metrics.count("test.span.counter", 1)

    sentry_sdk.flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 1

    assert metrics[0]["trace_id"] is not None
    assert metrics[0]["trace_id"] == segment.trace_id
    assert metrics[0]["span_id"] == segment.span_id


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


@pytest.mark.tests_internal_exceptions
def test_metrics_before_send_raises_does_not_crash_application(
    sentry_init, capture_items
):
    def _before_metric(record, hint):
        raise ValueError("before_send_metric error")

    sentry_init(
        before_send_metric=_before_metric,
    )
    items = capture_items("trace_metric")

    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    # The exception in before_send_metric is swallowed and the metric is
    # dropped.
    metrics = [item.payload for item in items]
    assert not metrics


@pytest.mark.tests_internal_exceptions
def test_metrics_before_send_raises_records_callback_error(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    def _before_metric(record, hint):
        raise ValueError("before_send_metric error")

    sentry_init(before_send_metric=_before_metric)
    items = capture_items("trace_metric")
    record_lost_event_calls = capture_record_lost_event_calls()

    sentry_sdk.metrics.count("test.keep", 1)

    get_client().flush()

    assert not items
    (lost_event_call,) = [
        call for call in record_lost_event_calls if call[0] == "callback_error"
    ]
    reason, data_category, item, quantity = lost_event_call
    assert data_category == "trace_metric"
    assert quantity == 1
    assert item.type == "trace_metric"
    assert item.headers["item_count"] == 1
    assert item.payload.json["items"][0]["name"] == "test.keep"


def test_metrics_before_send_returns_none_records_before_send(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    def _before_metric(record, hint):
        return None

    sentry_init(before_send_metric=_before_metric)
    items = capture_items("trace_metric")
    record_lost_event_calls = capture_record_lost_event_calls()

    sentry_sdk.metrics.count("test.drop", 1)

    get_client().flush()

    assert not items
    (lost_event_call,) = [
        call for call in record_lost_event_calls if call[0] == "before_send"
    ]
    reason, data_category, item, quantity = lost_event_call
    assert data_category == "trace_metric"
    assert quantity == 1
    assert item.type == "trace_metric"
    assert item.headers["item_count"] == 1
    assert item.payload.json["items"][0]["name"] == "test.drop"


def test_transport_format(sentry_init, capture_envelopes):
    sentry_init(server_name="test-server", release="1.0.0")

    envelopes = capture_envelopes()

    sentry_sdk.metrics.count("test.counter", 1)

    sentry_sdk.flush()

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
        "version": 2,
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
                    "process.runtime.name": {
                        "type": "string",
                        "value": mock.ANY,
                    },
                    "process.runtime.version": {
                        "type": "string",
                        "value": mock.ANY,
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
        ],
    }


def test_batcher_drops_metrics(sentry_init, monkeypatch):
    sentry_init()
    client = sentry_sdk.get_client()

    def no_op_flush():
        pass

    monkeypatch.setattr(client.metrics_batcher, "_flush", no_op_flush)

    lost_event_calls = []

    def record_lost_event(reason, data_category=None, item=None, *, quantity=1):
        lost_event_calls.append((reason, data_category, item, quantity))

    monkeypatch.setattr(client.metrics_batcher, "_record_lost_func", record_lost_event)

    for i in range(10_005):  # 5 metrics over the hard limit
        sentry_sdk.metrics.count("test.counter", 1)

    assert len(lost_event_calls) == 5
    for lost_event_call in lost_event_calls:
        reason, data_category, item, quantity = lost_event_call

        assert reason == "queue_overflow"
        assert data_category == "trace_metric"
        assert quantity == 1

        assert item.type == "trace_metric"
        assert item.headers == {
            "type": "trace_metric",
            "item_count": 1,
            "content_type": "application/vnd.sentry.items.trace-metric+json",
        }
        assert item.payload.json["items"][0]["name"] == "test.counter"


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


def test_array_attributes_deep_copied_in_before_send(sentry_init):
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
def test_metrics_window_sampling_state_reset_in_child_after_fork(sentry_init):
    """The child must not inherit the parent's queued metrics, sampling window
    or flusher thread, and must be able to start its own flusher."""
    sentry_init(
        _experiments={"metrics_window_sampling": {"per_name": {"test.sampled": 2}}}
    )
    batcher = sentry_sdk.get_client().metrics_batcher
    assert isinstance(batcher, LockFreeMetricsBatcher)

    sentry_sdk.metrics.count("test.sampled", 1)
    sentry_sdk.metrics.count("test.unsampled", 1)
    original_window = batcher._window
    batcher._retire_window(time.monotonic(), force=True)
    batcher._active.flag = True
    batcher._flush_event.set()
    batcher._running = False

    pid = os.fork()
    if pid == 0:
        reset = (
            batcher._flusher is None
            and batcher._flusher_pid is None
            and len(batcher._queue) == 0
            and len(batcher._retired) == 0
            and batcher._window is not original_window
            and not batcher._window.reservoirs
            and not getattr(batcher._active, "flag", False)
            and not batcher._flush_event.is_set()
            and batcher._running is True
        )
        restarted = batcher._ensure_thread() and batcher._flusher_pid == os.getpid()
        os._exit(0 if reset and restarted else 1)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def test_metrics_window_sampling_concurrent_adds(
    sentry_init, capture_items, capture_record_lost_event_calls, monkeypatch
):
    # Keep everything in one window, however slow the interleaved adds are
    monkeypatch.setattr(sentry_sdk._lock_free_metrics_batcher, "WINDOW_SECONDS", 60.0)
    # Small enough that the queue overflows, so both paths race
    monkeypatch.setattr(LockFreeMetricsBatcher, "MAX_BEFORE_DROP", 2000)
    monkeypatch.setattr(LockFreeMetricsBatcher, "MAX_BEFORE_FLUSH", 500)

    sentry_init(
        _experiments={"metrics_window_sampling": {"per_name": {"test.hot": 1 / 6}}}
    )
    items = capture_items("trace_metric")
    record_lost_event_calls = capture_record_lost_event_calls()

    num_threads, per_thread = 8, 1000

    def add_metrics():
        for _ in range(per_thread):
            sentry_sdk.metrics.count("test.hot", 1)
            sentry_sdk.metrics.count("test.cold", 1)

    # Switch threads as often as possible to interleave adds
    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=add_metrics) for _ in range(num_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(original_interval)

    get_client().flush()

    total = num_threads * per_thread

    def lost(reason):
        return sum(
            quantity
            for lost_reason, data_category, _, quantity in record_lost_event_calls
            if lost_reason == reason and data_category == "trace_metric"
        )

    def sent(name):
        return [item.payload for item in items if item.payload["name"] == name]

    # Every unsampled metric is either sent or reported as overflow, exactly once
    assert len(sent("test.cold")) + lost("queue_overflow") == total

    # Every sampled metric is either kept or reported as sampled out
    hot = sent("test.hot")
    assert 1 <= len(hot) <= 10
    assert len(hot) + lost("sample_rate") == total
    for metric in hot:
        assert metric["attributes"]["sentry.client_sample_rate"] == len(hot) / total


def test_metrics_not_sampled_by_default(sentry_init, capture_items):
    sentry_init()
    items = capture_items("trace_metric")

    for _ in range(100):
        sentry_sdk.metrics.count("test.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 100

    for metric in metrics:
        assert "sentry.client_sample_rate" not in metric["attributes"]


def test_metrics_window_sampling_basics(
    sentry_init, capture_items, capture_record_lost_event_calls
):
    sentry_init(_experiments={"metrics_window_sampling": True})
    items = capture_items("trace_metric")
    record_lost_event_calls = capture_record_lost_event_calls()

    for _ in range(200):
        sentry_sdk.metrics.count("test.hot", 1)
    for _ in range(3):
        sentry_sdk.metrics.count("test.cold", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 13

    hot = [metric for metric in metrics if metric["name"] == "test.hot"]
    assert len(hot) == 10
    for metric in hot:
        assert metric["attributes"]["sentry.client_sample_rate"] == 0.05

    cold = [metric for metric in metrics if metric["name"] == "test.cold"]
    assert len(cold) == 3
    for metric in cold:
        assert "sentry.client_sample_rate" not in metric["attributes"]

    assert record_lost_event_calls == [
        ("sample_rate", "trace_metric", None, 190),
        ("sample_rate", "trace_metric_byte", None, mock.ANY),
    ]


@pytest.mark.parametrize(
    "sample_per_second,expected_count",
    [
        (4, 20),
        (0.2, 1),
        (0.05, 1),
        (float("inf"), 10),
        (float("nan"), 10),
        (0, 10),
        (-1, 10),
        ("fast", 10),
    ],
)
def test_metrics_window_sampling_sample_per_second(
    sentry_init, capture_items, sample_per_second, expected_count
):
    sentry_init(
        _experiments={
            "metrics_window_sampling": {"sample_per_second": sample_per_second}
        }
    )
    items = capture_items("trace_metric")

    for _ in range(200):
        sentry_sdk.metrics.count("test.counter", 1)

    get_client().flush()

    assert len(items) == expected_count


def test_metrics_window_sampling_per_name(sentry_init, capture_items):
    sentry_init(
        _experiments={"metrics_window_sampling": {"per_name": {"test.hot": 0.2}}}
    )
    items = capture_items("trace_metric")

    for _ in range(200):
        sentry_sdk.metrics.count("test.hot", 1)
        sentry_sdk.metrics.count("test.other", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 201

    (hot,) = [metric for metric in metrics if metric["name"] == "test.hot"]
    assert hot["attributes"]["sentry.client_sample_rate"] == 1 / 200

    other = [metric for metric in metrics if metric["name"] == "test.other"]
    for metric in other:
        assert "sentry.client_sample_rate" not in metric["attributes"]


@pytest.mark.parametrize(
    "client_sample_rate,expected_sample_rate",
    [
        (0.5, 0.05),
        (5, 0.1),
        (0, 0.1),
        (-1, 0.1),
        (float("nan"), 0.1),
        ("0.5", 0.1),
    ],
)
def test_metrics_window_sampling_multiplies_client_sample_rate(
    sentry_init, capture_items, client_sample_rate, expected_sample_rate
):
    sentry_init(_experiments={"metrics_window_sampling": True})
    items = capture_items("trace_metric")

    for _ in range(100):
        sentry_sdk.metrics.count(
            "test.counter",
            1,
            attributes={"sentry.client_sample_rate": client_sample_rate},
        )

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 10

    for metric in metrics:
        assert metric["attributes"]["sentry.client_sample_rate"] == expected_sample_rate


def test_metrics_window_sampling_new_window(sentry_init, capture_items, monkeypatch):
    # Shorten the window so that the test doesn't have to wait 5 seconds for it
    # to close
    monkeypatch.setattr(sentry_sdk._lock_free_metrics_batcher, "WINDOW_SECONDS", 1.0)

    sentry_init(_experiments={"metrics_window_sampling": {"sample_per_second": 10}})
    items = capture_items("trace_metric")

    for _ in range(30):
        sentry_sdk.metrics.count("test.counter", 1)
    time.sleep(1.1)
    for _ in range(30):
        sentry_sdk.metrics.count("test.counter", 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 20

    for metric in metrics:
        assert metric["attributes"]["sentry.client_sample_rate"] == 1 / 3


def test_metrics_window_sampling_does_not_cap_names(sentry_init, capture_items):
    sentry_init(_experiments={"metrics_window_sampling": True})
    items = capture_items("trace_metric")

    for i in range(600):
        sentry_sdk.metrics.count(f"test.counter{i}", 1)
        sentry_sdk.metrics.count(f"test.counter{i}", 1)

    get_client().flush()

    assert len(items) == 1200


def test_metrics_window_sampling_drops_after_max_reached(
    sentry_init, capture_items, capture_record_lost_event_calls, monkeypatch
):
    monkeypatch.setattr(LockFreeMetricsBatcher, "MAX_BEFORE_DROP", 25)

    sentry_init(_experiments={"metrics_window_sampling": True})
    items = capture_items("trace_metric")
    record_lost_event_calls = capture_record_lost_event_calls()

    for name in ("test.a", "test.b", "test.c"):
        for _ in range(10):
            sentry_sdk.metrics.count(name, 1)

    get_client().flush()

    metrics = [item.payload for item in items]
    assert len(metrics) == 25

    # No name went over its limit, so nothing was sampled and the only loss is
    # the overflow
    for metric in metrics:
        assert "sentry.client_sample_rate" not in metric["attributes"]

    assert len(record_lost_event_calls) == 5
    for lost_event_call in record_lost_event_calls:
        reason, data_category, item, quantity = lost_event_call

        assert reason == "queue_overflow"
        assert data_category == "trace_metric"
        assert quantity == 1
        assert item.payload.json["items"][0]["name"] == "test.c"


def test_metrics_window_sampling_after_before_send_metric(sentry_init, capture_items):
    before_send_metric_calls = 0

    def _before_metric(record, hint):
        nonlocal before_send_metric_calls
        before_send_metric_calls += 1
        return record

    sentry_init(
        before_send_metric=_before_metric,
        _experiments={"metrics_window_sampling": True},
    )
    items = capture_items("trace_metric")

    for _ in range(200):
        sentry_sdk.metrics.count("test.counter", 1)

    get_client().flush()

    # Sampling happens after before_send_metric, so it still sees every metric
    assert before_send_metric_calls == 200

    metrics = [item.payload for item in items]
    assert len(metrics) == 10
