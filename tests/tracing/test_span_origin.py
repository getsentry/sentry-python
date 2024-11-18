from sentry_sdk import start_span


def test_span_origin_manual(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="foo", name="bar"):
            pass

    (event,) = events

    assert len(events) == 1
    assert event["spans"][0]["origin"] == "manual"
    assert event["contexts"]["trace"]["origin"] == "manual"


def test_span_origin_custom(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="foo", name="bar", origin="foo.foo2.foo3"):
            pass

    with start_span(name="ho", origin="ho.ho2.ho3"):
        with start_span(op="baz", name="qux", origin="baz.baz2.baz3"):
            pass

    (first_transaction, second_transaction) = events

    assert len(events) == 2
    assert first_transaction["contexts"]["trace"]["origin"] == "manual"
    assert first_transaction["spans"][0]["origin"] == "foo.foo2.foo3"

    assert second_transaction["contexts"]["trace"]["origin"] == "ho.ho2.ho3"
    assert second_transaction["spans"][0]["origin"] == "baz.baz2.baz3"
