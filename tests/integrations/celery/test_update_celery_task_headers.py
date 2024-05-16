from copy import copy
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

    updated_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    assert headers == {}  # left unchanged

    if monitor_beat_tasks:
        assert updated_headers == {
            "headers": {"sentry-monitor-start-timestamp-s": mock.ANY},
            "sentry-monitor-start-timestamp-s": mock.ANY,
        }
    else:
        assert updated_headers == headers


@pytest.mark.parametrize("monitor_beat_tasks", [True, False, None, "", "bla", 1, 0])
def test_monitor_beat_tasks_with_headers(monitor_beat_tasks):
    headers = {
        "blub": "foo",
        "sentry-something": "bar",
    }
    span = None

    updated_headers = _update_celery_task_headers(headers, span, monitor_beat_tasks)

    if monitor_beat_tasks:
        assert updated_headers == {
            "blub": "foo",
            "sentry-something": "bar",
            "headers": {
                "sentry-monitor-start-timestamp-s": mock.ANY,
                "sentry-something": "bar",
            },
            "sentry-monitor-start-timestamp-s": mock.ANY,
        }
    else:
        assert updated_headers == headers


def test_span_with_transaction(sentry_init):
    sentry_init(enable_tracing=True)
    headers = {}

    with sentry_sdk.start_transaction(name="test_transaction") as transaction:
        with sentry_sdk.start_span(op="test_span") as span:
            updated_headers = _update_celery_task_headers(headers, span, False)

            assert updated_headers["sentry-trace"] == span.to_traceparent()
            assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()
            assert updated_headers["baggage"] == transaction.get_baggage().serialize()
            assert (
                updated_headers["headers"]["baggage"]
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
            updated_headers = _update_celery_task_headers(headers, span, False)

            assert updated_headers["sentry-trace"] == span.to_traceparent()
            assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()

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
            assert updated_headers["baggage"] == combined_baggage.serialize(
                include_third_party=True
            )
            assert updated_headers["headers"]["baggage"] == combined_baggage.serialize(
                include_third_party=True
            )
