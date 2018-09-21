from sentry_sdk import (
    push_scope,
    configure_scope,
    capture_exception,
    add_breadcrumb,
    last_event_id,
    Hub,
)


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
    assert last_event_id() == event_id
    assert Hub.current.last_event_id() == event_id


def test_option_callback(sentry_init, capture_events):
    drop_events = False
    drop_breadcrumbs = False

    def before_send(event, hint):
        assert isinstance(hint["exc_info"][1], ValueError)
        if not drop_events:
            event["extra"] = {"foo": "bar"}
            return event

    def before_breadcrumb(crumb, hint):
        assert hint == {"foo": 42}
        if not drop_breadcrumbs:
            crumb["data"] = {"foo": "bar"}
            return crumb

    sentry_init(before_send=before_send, before_breadcrumb=before_breadcrumb)
    events = capture_events()

    def do_this():
        add_breadcrumb(message="Hello", hint={"foo": 42})
        try:
            raise ValueError("aha!")
        except Exception:
            capture_exception()

    do_this()
    drop_breadcrumbs = True
    do_this()
    drop_events = True
    do_this()

    normal, no_crumbs = events

    assert normal["exception"]["values"][0]["type"] == "ValueError"
    crumb, = normal["breadcrumbs"]
    assert "timestamp" in crumb
    assert crumb["message"] == "Hello"
    assert crumb["data"] == {"foo": "bar"}
    assert crumb["type"] == "default"


def test_breadcrumb_arguments(sentry_init, capture_events):
    assert_hint = {"bar": 42}

    def before_breadcrumb(crumb, hint):
        assert crumb["foo"] == 42
        assert hint == assert_hint

    sentry_init(before_breadcrumb=before_breadcrumb)

    add_breadcrumb(foo=42, hint=dict(bar=42))
    add_breadcrumb(dict(foo=42), dict(bar=42))
    add_breadcrumb(dict(foo=42), hint=dict(bar=42))
    add_breadcrumb(crumb=dict(foo=42), hint=dict(bar=42))

    assert_hint.clear()
    add_breadcrumb(foo=42)
    add_breadcrumb(crumb=dict(foo=42))


def test_push_scope(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    with push_scope() as scope:
        scope.level = "warning"
        try:
            1 / 0
        except Exception as e:
            capture_exception(e)

    event, = events

    assert event["level"] == "warning"
    assert "exception" in event


def test_push_scope_null_client(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    Hub.current.bind_client(None)

    with push_scope() as scope:
        scope.level = "warning"
        try:
            1 / 0
        except Exception as e:
            capture_exception(e)

    assert len(events) == 0


def test_breadcrumbs(sentry_init, capture_events):
    sentry_init(max_breadcrumbs=10)
    events = capture_events()

    for i in range(20):
        add_breadcrumb(
            category="auth", message="Authenticated user %s" % i, level="info"
        )

    capture_exception(ValueError())
    event, = events

    assert len(event["breadcrumbs"]) == 10
    assert "user 10" in event["breadcrumbs"][0]["message"]
    assert "user 19" in event["breadcrumbs"][-1]["message"]

    events.clear()

    for i in range(2):
        add_breadcrumb(
            category="auth", message="Authenticated user %s" % i, level="info"
        )

    with configure_scope() as scope:
        scope.clear()

    capture_exception(ValueError())
    event, = events
    assert len(event["breadcrumbs"]) == 0
