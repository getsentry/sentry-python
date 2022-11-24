from sentry_sdk.tracing import Transaction


def test_extract_sentry_trace():
    # type: () -> None

    trace_id, parent_span_id, parent_sampled = Transaction.extract_sentry_trace(
        "4c79f60c11214eb38604f4ae0781bfb2-fa90fdead5f74052-1"
    )
    assert trace_id == "4c79f60c11214eb38604f4ae0781bfb2"
    assert parent_span_id == "fa90fdead5f74052"
    assert parent_sampled

    trace_id, parent_span_id, parent_sampled = Transaction.extract_sentry_trace(
        "5e79f60c11214eb38604f4ae0781bfb2-0390fdead5f74052-0"
    )
    assert trace_id == "5e79f60c11214eb38604f4ae0781bfb2"
    assert parent_span_id == "0390fdead5f74052"
    assert not parent_sampled
