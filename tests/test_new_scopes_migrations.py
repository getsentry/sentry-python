import pytest

import sentry_sdk


def test_configure_scope_before(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with sentry_sdk.configure_scope() as scope:
        sentry_sdk.set_tag("B", 1)
        scope.set_tag("BB", 1)
        sentry_sdk.capture_message("Event B")

    scope.set_tag("C", 1)
    sentry_sdk.capture_message("Event C")

    (event_a, event_b, event_c) = events
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B": 1, "BB": 1}
    assert event_c["tags"] == {"A": 1, "B": 1, "BB": 1, "C": 1}


def test_configure_scope_after(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Hi A")

    with sentry_sdk.new_scope() as scope:
        sentry_sdk.set_tag("B", 1)
        scope.set_tag("BB", 1)
        sentry_sdk.capture_message("Hi B")

    scope.set_tag("C", 1)
    sentry_sdk.capture_message("Hi C")

    (event_a, event_b, event_c) = events
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B": 1, "BB": 1}
    assert event_c["tags"] == {"A": 1, "B": 1, "BB": 1, "C": 1}


# def test_X_before(sentry_init, capture_events, capture_envelopes, ):
#     sentry_init(traces_sample_rate=1.0)

#     sentry_sdk.set_tag("A", 1)


# def test_X_after(sentry_init, capture_events, capture_envelopes, ):
#     sentry_init(traces_sample_rate=1.0)

#     sentry_sdk.set_tag("A", 1)

