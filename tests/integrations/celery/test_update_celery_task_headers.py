import pytest

from unittest import mock

from sentry_sdk.integrations.celery import _update_celery_task_headers
import sentry_sdk


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


def test_span_with_no_transaction(sentry_init):
    sentry_init(enable_tracing=True)
    headers = {}

    with sentry_sdk.start_span(op="test_span") as span:
        updated_headers = _update_celery_task_headers(headers, span, False)

        assert updated_headers["sentry-trace"] == span.to_traceparent()
        assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()
        assert "baggage" not in updated_headers.keys()
        assert "baggage" not in updated_headers["headers"].keys()


def test_custom_span(sentry_init):
    sentry_init(enable_tracing=True)
    span = sentry_sdk.tracing.Span()
    headers = {}

    with sentry_sdk.start_transaction(name="test_transaction"):
        updated_headers = _update_celery_task_headers(headers, span, False)

        assert updated_headers["sentry-trace"] == span.to_traceparent()
        assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()
        assert "baggage" not in updated_headers.keys()
        assert "baggage" not in updated_headers["headers"].keys()


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
            # This is probably the cause for https://github.com/getsentry/sentry-python/issues/2916
            # If incoming baggage includes sentry data, we should not concatenate a new baggage value to it
            # but just keep the incoming sentry baggage values and concatenate new third-party items to the baggage
            # I have some code somewhere where I have implemented this.
            assert (
                updated_headers["baggage"]
                == headers["baggage"] + "," + transaction.get_baggage().serialize()
            )
            assert (
                updated_headers["headers"]["baggage"]
                == headers["baggage"] + "," + transaction.get_baggage().serialize()
            )


def test_span_with_no_transaction_custom_headers(sentry_init):
    sentry_init(enable_tracing=True)
    headers = {
        "baggage": BAGGAGE_VALUE,
        "sentry-trace": SENTRY_TRACE_VALUE,
    }

    with sentry_sdk.start_span(op="test_span") as span:
        updated_headers = _update_celery_task_headers(headers, span, False)

        assert updated_headers["sentry-trace"] == span.to_traceparent()
        assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()
        assert updated_headers["baggage"] == headers["baggage"]
        assert updated_headers["headers"]["baggage"] == headers["baggage"]


def test_custom_span_custom_headers(sentry_init):
    sentry_init(enable_tracing=True)
    span = sentry_sdk.tracing.Span()
    headers = {
        "baggage": BAGGAGE_VALUE,
        "sentry-trace": SENTRY_TRACE_VALUE,
    }

    with sentry_sdk.start_transaction(name="test_transaction"):
        updated_headers = _update_celery_task_headers(headers, span, False)

        assert updated_headers["sentry-trace"] == span.to_traceparent()
        assert updated_headers["headers"]["sentry-trace"] == span.to_traceparent()
        assert updated_headers["baggage"] == headers["baggage"]
        assert updated_headers["headers"]["baggage"] == headers["baggage"]
