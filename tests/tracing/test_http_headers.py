import pytest

from sentry_sdk.tracing import Transaction
from sentry_sdk.tracing_utils import extract_sentrytrace_data


try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.mark.parametrize("sampled", [True, False, None])
def test_to_traceparent(sentry_init, sampled):
    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
        sampled=sampled,
    )

    traceparent = transaction.to_traceparent()

    trace_id, parent_span_id, parent_sampled = traceparent.split("-")
    assert trace_id == "12312012123120121231201212312012"
    assert parent_span_id == transaction.span_id
    assert parent_sampled == (
        "1" if sampled is True else "0" if sampled is False else ""
    )


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


def test_iter_headers(sentry_init, monkeypatch):
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
