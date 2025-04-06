from unittest import mock

import pytest

from sentry_sdk.tracing import Transaction
from sentry_sdk.tracing_utils import (
    extract_sentrytrace_data,
    extract_w3c_traceparent_data,
)


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


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_w3c_traceparent_extraction(sampling_decision):
    traceparent_header = (
        "00-d00afb0f1514f9337a4a921c514955db-903c8c4987adea4b-{}".format(
            "01" if sampling_decision is True else "00"
        )
    )
    assert extract_w3c_traceparent_data(traceparent_header) == {
        "trace_id": "d00afb0f1514f9337a4a921c514955db",
        "parent_span_id": "903c8c4987adea4b",
        "parent_sampled": sampling_decision,
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
