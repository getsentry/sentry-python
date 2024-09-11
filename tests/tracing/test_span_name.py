import pytest

import sentry_sdk


def test_start_span_description(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with pytest.deprecated_call():
            with sentry_sdk.start_span(op="foo", description="span-desc"):
                ...

    (event,) = events

    assert event["spans"][0]["description"] == "span-desc"


def test_start_span_name(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with sentry_sdk.start_span(op="foo", name="span-name"):
            ...

    (event,) = events

    assert event["spans"][0]["description"] == "span-name"


def test_start_child_description(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with pytest.deprecated_call():
            with sentry_sdk.start_span(op="foo", description="span-desc") as span:
                with span.start_child(op="bar", description="child-desc"):
                    ...

    (event,) = events

    assert event["spans"][-1]["description"] == "child-desc"


def test_start_child_name(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with sentry_sdk.start_transaction(name="hi"):
        with sentry_sdk.start_span(op="foo", name="span-name") as span:
            with span.start_child(op="bar", name="child-name"):
                ...

    (event,) = events

    assert event["spans"][-1]["description"] == "child-name"
