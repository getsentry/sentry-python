from sentry_sdk import start_span

from sentry_sdk.tracing import Span


def test_start_span_to_start_transaction(sentry_init, capture_events):
    # XXX: this only exists for backwards compatibility with code before
    # Transaction / start_transaction were introduced.
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(transaction="/1/"):
        pass

    with start_span(Span(transaction="/2/")):
        pass

    assert len(events) == 2
    assert events[0]["transaction"] == "/1/"
    assert events[1]["transaction"] == "/2/"
