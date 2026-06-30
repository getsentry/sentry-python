import sentry_sdk


def test_span_origin_manual(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with sentry_sdk.start_span(op="foo", name="bar"):
            pass

    (event,) = events

    assert len(events) == 1
    assert event["spans"][0]["origin"] == "manual"
    assert event["contexts"]["trace"]["origin"] == "manual"


def test_span_origin_custom(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with sentry_sdk.start_span(op="foo", name="bar", origin="foo.foo2.foo3"):
            pass

    with sentry_sdk.start_transaction(name="ho", origin="ho.ho2.ho3"):
        with sentry_sdk.start_span(op="baz", name="qux", origin="baz.baz2.baz3"):
            pass

    (first_transaction, second_transaction) = events

    assert len(events) == 2
    assert first_transaction["contexts"]["trace"]["origin"] == "manual"
    assert first_transaction["spans"][0]["origin"] == "foo.foo2.foo3"

    assert second_transaction["contexts"]["trace"]["origin"] == "ho.ho2.ho3"
    assert second_transaction["spans"][0]["origin"] == "baz.baz2.baz3"


def test_span_origin_manual_span_streaming(sentry_init, capture_items):
    sentry_init(_experiments={"trace_lifecycle": "stream"}, traces_sample_rate=1.0)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="hi"):
        pass

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]

    assert len(items) == 1
    assert span["attributes"]["sentry.origin"] == "manual"


def test_span_origin_custom_span_streaming(sentry_init, capture_items):
    sentry_init(_experiments={"trace_lifecycle": "stream"}, traces_sample_rate=1.0)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(
        name="hi", attributes={"sentry.origin": "foo.foo2.foo3"}
    ):
        pass

    with sentry_sdk.traces.start_span(
        name="ho", attributes={"sentry.origin": "baz.baz2.baz3"}
    ):
        pass

    sentry_sdk.flush()

    (span1, span2) = [item.payload for item in items]

    assert len(items) == 2
    assert span1["attributes"]["sentry.origin"] == "foo.foo2.foo3"
    assert span2["attributes"]["sentry.origin"] == "baz.baz2.baz3"
