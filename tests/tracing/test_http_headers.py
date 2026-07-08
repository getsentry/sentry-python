from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Transaction
from sentry_sdk.tracing_utils import extract_sentrytrace_data


@pytest.mark.parametrize("sampled", [True, False, None])
def test_to_traceparent(sampled):
    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
        sampled=sampled,
    )

    traceparent = transaction.to_traceparent()

    parts = traceparent.split("-")
    assert parts[0] == "12312012123120121231201212312012"  # trace_id
    assert parts[1] == transaction.span_id  # parent_span_id
    if sampled is None:
        assert len(parts) == 2
    else:
        assert parts[2] == "1" if sampled is True else "0"  # sampled


@pytest.mark.parametrize("traces_sample_rate", [1.0, 0.0, None])
def test_to_traceparent_span_streaming(sentry_init, traces_sample_rate):
    sentry_init(
        traces_sample_rate=traces_sample_rate,
        _experiments={
            "trace_lifecycle": "stream",
        },
    )

    with sentry_sdk.traces.start_span(name="/interactions/other-dogs/new-dog") as span:
        traceparent = sentry_sdk.get_traceparent()

        parts = traceparent.split("-")
        if traces_sample_rate is None:
            propagation_context = (
                sentry_sdk.get_current_scope().get_active_propagation_context()
            )
            assert parts[0] == propagation_context.trace_id  # trace_id
            assert parts[1] == propagation_context.span_id  # parent_span_id
            assert len(parts) == 2
        else:
            assert parts[0] == span.trace_id  # trace_id
            assert parts[1] == span.span_id  # parent_span_id
            assert parts[2] == "1" if traces_sample_rate == 1.0 else "0"  # sampled


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_sentrytrace_extraction(sampling_decision):
    sentrytrace_header = "12312012123120121231201212312012-0415201309082013-{}".format(
        1 if sampling_decision is True else 0
    )
    assert extract_sentrytrace_data(sentrytrace_header) == {
        "trace_id": "12312012123120121231201212312012",
        "parent_span_id": "0415201309082013",
        "parent_sampled": sampling_decision,
    }


def test_sentrytrace_extraction_multiple_headers():
    sentrytrace_header = (
        "12312012123120121231201212312012-0415201309082013-1,"
        "12312012123120121231201212312012-0000000000000000-0"
    )
    assert extract_sentrytrace_data(sentrytrace_header) == {
        "trace_id": "12312012123120121231201212312012",
        "parent_span_id": "0415201309082013",
        "parent_sampled": True,
    }


def test_sentrytrace_extraction_leading_empty_fragment():
    sentrytrace_header = ",12312012123120121231201212312012-0415201309082013-1"
    assert extract_sentrytrace_data(sentrytrace_header) == {
        "trace_id": "12312012123120121231201212312012",
        "parent_span_id": "0415201309082013",
        "parent_sampled": True,
    }


def test_iter_headers(monkeypatch):
    monkeypatch.setattr(
        Transaction,
        "to_traceparent",
        mock.Mock(return_value="12312012123120121231201212312012-0415201309082013-0"),
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    )

    headers = dict(transaction.iter_headers())
    assert (
        headers["sentry-trace"] == "12312012123120121231201212312012-0415201309082013-0"
    )


def test_iter_headers_span_streaming(sentry_init, monkeypatch):
    sentry_init(
        traces_sample_rate=0.0,
        _experiments={
            "trace_lifecycle": "stream",
        },
    )
    monkeypatch.setattr(
        StreamedSpan,
        "_to_traceparent",
        mock.Mock(return_value="12312012123120121231201212312012-0415201309082013-0"),
    )

    with sentry_sdk.traces.start_span(name="/interactions/other-dogs/new-dog") as span:
        headers = dict(span._iter_headers())
        assert (
            headers["sentry-trace"]
            == "12312012123120121231201212312012-0415201309082013-0"
        )
