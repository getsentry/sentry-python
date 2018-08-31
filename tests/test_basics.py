from sentry_sdk import configure_scope, capture_exception, get_last_event_id, Hub


def test_processors(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    with configure_scope() as scope:

        def error_processor(event, exc_info):
            event["exception"]["values"][0]["value"] += " whatever"
            return event

        scope.add_error_processor(error_processor, ValueError)

    try:
        raise ValueError("aha!")
    except Exception:
        capture_exception()

    event, = events

    assert event["exception"]["values"][0]["value"] == "aha! whatever"


def test_event_id(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        raise ValueError("aha!")
    except Exception:
        event_id = capture_exception()
        int(event_id, 16)
        assert len(event_id) == 32

    event, = events
    assert event["event_id"] == event_id
    assert get_last_event_id() == event_id
    assert Hub.current.get_last_event_id() == event_id
