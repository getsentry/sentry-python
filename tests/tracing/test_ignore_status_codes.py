import sentry_sdk
from sentry_sdk import start_transaction, start_span

import pytest

from collections import Counter


def test_no_ignored_codes(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", 404)

    assert len(events) == 1


@pytest.mark.parametrize("status_code", [200, 404])
def test_single_code_ignored(sentry_init, capture_events, status_code):
    sentry_init(
        traces_sample_rate=1.0,
        trace_ignore_status_codes={
            404,
        },
    )
    events = capture_events()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", status_code)

    if status_code == 404:
        assert not events
    else:
        assert len(events) == 1


@pytest.mark.parametrize("status_code", [200, 305, 307, 399, 404])
def test_range_ignored(sentry_init, capture_events, status_code):
    sentry_init(
        traces_sample_rate=1.0,
        trace_ignore_status_codes=set(
            range(
                305,
                400,
            ),
        ),
    )
    events = capture_events()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", status_code)

    if 305 <= status_code <= 399:
        assert not events
    else:
        assert len(events) == 1


@pytest.mark.parametrize("status_code", [200, 301, 303, 355, 404])
def test_variety_ignored(sentry_init, capture_events, status_code):
    sentry_init(
        traces_sample_rate=1.0,
        trace_ignore_status_codes={
            301,
            302,
            303,
            *range(
                305,
                400,
            ),
            *range(
                401,
                405,
            ),
        },
    )
    events = capture_events()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", status_code)

    if (
        301 <= status_code <= 303
        or 305 <= status_code <= 399
        or 401 <= status_code <= 404
    ):
        assert not events
    else:
        assert len(events) == 1


def test_transaction_not_ignored_when_status_code_has_invalid_type(
    sentry_init, capture_events
):
    sentry_init(
        traces_sample_rate=1.0,
        trace_ignore_status_codes=set(
            range(401, 404),
        ),
    )
    events = capture_events()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", "404")

    assert len(events) == 1


def test_records_lost_events(sentry_init, capture_record_lost_event_calls):
    sentry_init(
        traces_sample_rate=1.0,
        trace_ignore_status_codes={
            404,
        },
    )
    record_lost_event_calls = capture_record_lost_event_calls()

    with start_transaction(op="http", name="GET /"):
        span_or_tx = sentry_sdk.get_current_span()
        span_or_tx.set_data("http.response.status_code", 404)

        with start_span(op="child-span"):
            with start_span(op="child-child-span"):
                pass

    assert Counter(record_lost_event_calls) == Counter(
        [
            ("event_processor", "transaction", None, 1),
            ("event_processor", "span", None, 3),
        ]
    )
