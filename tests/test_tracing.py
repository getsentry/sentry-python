import pytest

from sentry_sdk import Hub


@pytest.mark.parametrize("sample_rate", [0.0, 1.0])
def test_basic(sentry_init, capture_events, sample_rate):
    sentry_init(traces_sample_rate=sample_rate)
    events = capture_events()

    with Hub.current.trace(transaction="hi"):
        with pytest.raises(ZeroDivisionError):
            with Hub.current.span():
                1 / 0

        with Hub.current.span():
            pass

    if sample_rate:
        event, = events

        span1, span2, parent_span = event["spans"]
        assert not span1["tags"]["success"]
        assert span2["tags"]["success"]
        assert parent_span["transaction"] == "hi"
    else:
        assert not events
