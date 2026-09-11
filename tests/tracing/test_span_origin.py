import sentry_sdk


def test_span_origin_manual(sentry_init, capture_items):
    sentry_init(trace_lifecycle="stream", traces_sample_rate=1.0)
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="hi"):
        pass

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]

    assert len(items) == 1
    assert span["attributes"]["sentry.origin"] == "manual"


def test_span_origin_custom(sentry_init, capture_items):
    sentry_init(trace_lifecycle="stream", traces_sample_rate=1.0)
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
