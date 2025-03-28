from copy import copy
import itertools
import pytest

from unittest import mock

from sentry_sdk.integrations.celery import _update_celery_task_headers
import sentry_sdk
from sentry_sdk.tracing_utils import Baggage


BAGGAGE_VALUE = (
    "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
    "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
    "sentry-sample_rate=0.1337,"
    "custom=value"
)

SENTRY_TRACE_VALUE = "771a43a4192642f0b136d5159a501700-1234567890abcdef-1"


@pytest.mark.parametrize("monitor_beat_tasks", [True, False, None, "", "bla", 1, 0])
def test_monitor_beat_tasks(monitor_beat_tasks):
    headers = {}
    span = None

    outgoing_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert headers == {}  # left unchanged

    if monitor_beat_tasks:
        assert outgoing_headers["sentry-monitor-start-timestamp-s"] == mock.ANY
        assert (
            outgoing_headers["headers"]["sentry-monitor-start-timestamp-s"] == mock.ANY
        )
    else:
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers["headers"]


@pytest.mark.parametrize("monitor_beat_tasks", [True, False, None, "", "bla", 1, 0])
def test_monitor_beat_tasks_with_headers(monitor_beat_tasks):
    headers = {
        "blub": "foo",
        "sentry-something": "bar",
        "sentry-task-enqueued-time": mock.ANY,
    }
    span = None

    outgoing_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert headers == {
        "blub": "foo",
        "sentry-something": "bar",
        "sentry-task-enqueued-time": mock.ANY,
    }  # left unchanged

    if monitor_beat_tasks:
        assert outgoing_headers["blub"] == "foo"
        assert outgoing_headers["sentry-something"] == "bar"
        assert outgoing_headers["sentry-monitor-start-timestamp-s"] == mock.ANY
        assert outgoing_headers["headers"]["sentry-something"] == "bar"
        assert (
            outgoing_headers["headers"]["sentry-monitor-start-timestamp-s"] == mock.ANY
        )
    else:
        assert outgoing_headers["blub"] == "foo"
        assert outgoing_headers["sentry-something"] == "bar"
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers["headers"]


def test_span_with_transaction(sentry_init):
    sentry_init(enable_tracing=True)
    headers = {}
    monitor_beat_tasks = False

    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        with sentry_sdk.start_span(op="test_span") as span:
            outgoing_headers = _update_celery_task_headers(
                headers, span, monitor_beat_tasks
            )

            assert outgoing_headers["sentry-trace"] == span.to_traceparent()
            assert outgoing_headers["headers"]["sentry-trace"] == span.to_traceparent()
            assert outgoing_headers["baggage"] == transaction.get_baggage().serialize()
            assert (
                outgoing_headers["headers"]["baggage"]
                == transaction.get_baggage().serialize()
            )


def test_span_with_transaction_custom_headers(sentry_init):
    sentry_init(enable_tracing=True)
    headers = {
        "baggage": BAGGAGE_VALUE,
        "sentry-trace": SENTRY_TRACE_VALUE,
    }

    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        with sentry_sdk.start_span(op="test_span") as span:
            outgoing_headers = _update_celery_task_headers(headers, span, False)

            assert outgoing_headers["sentry-trace"] == span.to_traceparent()
            assert outgoing_headers["headers"]["sentry-trace"] == span.to_traceparent()

            incoming_baggage = Baggage.from_incoming_header(headers["baggage"])
            combined_baggage = copy(transaction.get_baggage())
            combined_baggage.sentry_items.update(incoming_baggage.sentry_items)
            combined_baggage.third_party_items = ",".join(
                [
                    x
                    for x in [
                        combined_baggage.third_party_items,
                        incoming_baggage.third_party_items,
                    ]
                    if x is not None and x != ""
                ]
            )
            assert outgoing_headers["baggage"] == combined_baggage.serialize(
                include_third_party=True
            )
            assert outgoing_headers["headers"]["baggage"] == combined_baggage.serialize(
                include_third_party=True
            )


@pytest.mark.parametrize("monitor_beat_tasks", [True, False])
def test_celery_trace_propagation_default(sentry_init, monitor_beat_tasks):
    """
    The celery integration does not check the traces_sample_rate.
    By default traces_sample_rate is None which means "do not propagate traces".
    But the celery integration does not check this value.
    The Celery integration has its own mechanism to propagate traces:
    https://docs.sentry.io/platforms/python/integrations/celery/#distributed-traces
    """
    sentry_init()

    headers = {}
    span = None

    scope = sentry_sdk.get_isolation_scope()

    outgoing_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert outgoing_headers["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["headers"]["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["baggage"] == scope.get_baggage().serialize()
    assert outgoing_headers["headers"]["baggage"] == scope.get_baggage().serialize()

    if monitor_beat_tasks:
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers["headers"]
    else:
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers["headers"]


@pytest.mark.parametrize(
    "traces_sample_rate,monitor_beat_tasks",
    list(itertools.product([None, 0, 0.0, 0.5, 1.0, 1, 2], [True, False])),
)
def test_celery_trace_propagation_traces_sample_rate(
    sentry_init, traces_sample_rate, monitor_beat_tasks
):
    """
    The celery integration does not check the traces_sample_rate.
    By default traces_sample_rate is None which means "do not propagate traces".
    But the celery integration does not check this value.
    The Celery integration has its own mechanism to propagate traces:
    https://docs.sentry.io/platforms/python/integrations/celery/#distributed-traces
    """
    sentry_init(traces_sample_rate=traces_sample_rate)

    headers = {}
    span = None

    scope = sentry_sdk.get_isolation_scope()

    outgoing_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert outgoing_headers["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["headers"]["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["baggage"] == scope.get_baggage().serialize()
    assert outgoing_headers["headers"]["baggage"] == scope.get_baggage().serialize()

    if monitor_beat_tasks:
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers["headers"]
    else:
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers["headers"]


@pytest.mark.parametrize(
    "enable_tracing,monitor_beat_tasks",
    list(itertools.product([None, True, False], [True, False])),
)
def test_celery_trace_propagation_enable_tracing(
    sentry_init, enable_tracing, monitor_beat_tasks
):
    """
    The celery integration does not check the traces_sample_rate.
    By default traces_sample_rate is None which means "do not propagate traces".
    But the celery integration does not check this value.
    The Celery integration has its own mechanism to propagate traces:
    https://docs.sentry.io/platforms/python/integrations/celery/#distributed-traces
    """
    sentry_init(enable_tracing=enable_tracing)

    headers = {}
    span = None

    scope = sentry_sdk.get_isolation_scope()

    outgoing_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert outgoing_headers["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["headers"]["sentry-trace"] == scope.get_traceparent()
    assert outgoing_headers["baggage"] == scope.get_baggage().serialize()
    assert outgoing_headers["headers"]["baggage"] == scope.get_baggage().serialize()

    if monitor_beat_tasks:
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" in outgoing_headers["headers"]
    else:
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers
        assert "sentry-monitor-start-timestamp-s" not in outgoing_headers["headers"]
