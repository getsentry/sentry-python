from sentry_conventions.attributes import ATTRIBUTE_NAMES

import sentry_sdk


def test_span_origin_manual(sentry_init, capture_items):
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    with sentry_sdk.start_span(name="hi"):
        pass

    sentry_sdk.flush()

    (span,) = [item.payload for item in items]

    assert len(items) == 1
    assert span["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "manual"


def test_span_origin_custom(sentry_init, capture_items):
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    with sentry_sdk.start_span(
        name="hi", attributes={ATTRIBUTE_NAMES.SENTRY_ORIGIN: "foo.foo2.foo3"}
    ):
        pass

    with sentry_sdk.start_span(
        name="ho", attributes={ATTRIBUTE_NAMES.SENTRY_ORIGIN: "baz.baz2.baz3"}
    ):
        pass

    sentry_sdk.flush()

    (span1, span2) = [item.payload for item in items]

    assert len(items) == 2
    assert span1["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "foo.foo2.foo3"
    assert span2["attributes"][ATTRIBUTE_NAMES.SENTRY_ORIGIN] == "baz.baz2.baz3"
