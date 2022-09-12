import os
import sys
import logging

import pytest

from sentry_sdk import (
    Client,
    push_scope,
    configure_scope,
    capture_event,
    capture_exception,
    capture_message,
    start_transaction,
    add_breadcrumb,
    last_event_id,
    Hub,
)

from sentry_sdk._compat import reraise
from sentry_sdk.integrations import _AUTO_ENABLING_INTEGRATIONS
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scope import (  # noqa: F401
    add_global_event_processor,
    global_event_processors,
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

    (event,) = events

    assert event["exception"]["values"][0]["value"] == "aha! whatever"


def test_auto_enabling_integrations_catches_import_error(sentry_init, caplog):
    caplog.set_level(logging.DEBUG)
    REDIS = 12  # noqa: N806

    sentry_init(auto_enabling_integrations=True, debug=True)

    for import_string in _AUTO_ENABLING_INTEGRATIONS:
        # Ignore redis in the test case, because it is installed as a
        # dependency for running tests, and therefore always enabled.
        if _AUTO_ENABLING_INTEGRATIONS[REDIS] == import_string:
            continue

        assert any(
            record.message.startswith(
                "Did not import default integration {}:".format(import_string)
            )
            for record in caplog.records
        ), "Problem with checking auto enabling {}".format(import_string)


def test_event_id(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        raise ValueError("aha!")
    except Exception:
        event_id = capture_exception()
        int(event_id, 16)
        assert len(event_id) == 32

    (event,) = events
    assert event["event_id"] == event_id
    assert last_event_id() == event_id
    assert Hub.current.last_event_id() == event_id

    new_event_id = Hub.current.capture_event({"type": "transaction"})
    assert new_event_id is not None
    assert new_event_id != event_id
    assert Hub.current.last_event_id() == event_id


def test_option_callback(sentry_init, capture_events, monkeypatch):
    drop_events = False
    drop_breadcrumbs = False
    reports = []

    def record_lost_event(reason, data_category=None, item=None):
        reports.append((reason, data_category))

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

    monkeypatch.setattr(
        Hub.current.client.transport, "record_lost_event", record_lost_event
    )

    def do_this():
        add_breadcrumb(message="Hello", hint={"foo": 42})
        try:
            raise ValueError("aha!")
        except Exception:
            capture_exception()

    do_this()
    drop_breadcrumbs = True
    do_this()
    assert not reports
    drop_events = True
    do_this()
    assert reports == [("before_send", "error")]

    normal, no_crumbs = events

    assert normal["exception"]["values"][0]["type"] == "ValueError"
    (crumb,) = normal["breadcrumbs"]["values"]
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

    (event,) = events

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


@pytest.mark.parametrize("null_client", (True, False))
def test_push_scope_callback(sentry_init, null_client, capture_events):
    sentry_init()

    if null_client:
        Hub.current.bind_client(None)

    outer_scope = Hub.current.scope

    calls = []

    @push_scope
    def _(scope):
        assert scope is Hub.current.scope
        assert scope is not outer_scope
        calls.append(1)

    # push_scope always needs to execute the callback regardless of
    # client state, because that actually runs usercode in it, not
    # just scope config code
    assert calls == [1]

    # Assert scope gets popped correctly
    assert Hub.current.scope is outer_scope


def test_breadcrumbs(sentry_init, capture_events):
    sentry_init(max_breadcrumbs=10)
    events = capture_events()

    for i in range(20):
        add_breadcrumb(
            category="auth", message="Authenticated user %s" % i, level="info"
        )

    capture_exception(ValueError())
    (event,) = events

    assert len(event["breadcrumbs"]["values"]) == 10
    assert "user 10" in event["breadcrumbs"]["values"][0]["message"]
    assert "user 19" in event["breadcrumbs"]["values"][-1]["message"]

    del events[:]

    for i in range(2):
        add_breadcrumb(
            category="auth", message="Authenticated user %s" % i, level="info"
        )

    with configure_scope() as scope:
        scope.clear()

    capture_exception(ValueError())
    (event,) = events
    assert len(event["breadcrumbs"]["values"]) == 0


def test_attachments(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    this_file = os.path.abspath(__file__.rstrip("c"))

    with configure_scope() as scope:
        scope.add_attachment(bytes=b"Hello World!", filename="message.txt")
        scope.add_attachment(path=this_file)

    capture_exception(ValueError())

    (envelope,) = envelopes

    assert len(envelope.items) == 3
    assert envelope.get_event()["exception"] is not None

    attachments = [x for x in envelope.items if x.type == "attachment"]
    (message, pyfile) = attachments

    assert message.headers["filename"] == "message.txt"
    assert message.headers["type"] == "attachment"
    assert message.headers["content_type"] == "text/plain"
    assert message.payload.bytes == message.payload.get_bytes() == b"Hello World!"

    assert pyfile.headers["filename"] == os.path.basename(this_file)
    assert pyfile.headers["type"] == "attachment"
    assert pyfile.headers["content_type"].startswith("text/")
    assert pyfile.payload.bytes is None
    with open(this_file, "rb") as f:
        assert pyfile.payload.get_bytes() == f.read()


def test_integration_scoping(sentry_init, capture_events):
    logger = logging.getLogger("test_basics")

    # This client uses the logging integration
    logging_integration = LoggingIntegration(event_level=logging.WARNING)
    sentry_init(default_integrations=False, integrations=[logging_integration])
    events = capture_events()
    logger.warning("This is a warning")
    assert len(events) == 1

    # This client does not
    sentry_init(default_integrations=False)
    events = capture_events()
    logger.warning("This is not a warning")
    assert not events


def test_client_initialized_within_scope(sentry_init, caplog):
    caplog.set_level(logging.WARNING)

    sentry_init(debug=True)

    with push_scope():
        Hub.current.bind_client(Client())

    (record,) = (x for x in caplog.records if x.levelname == "WARNING")

    assert record.msg.startswith("init() called inside of pushed scope.")


def test_scope_leaks_cleaned_up(sentry_init, caplog):
    caplog.set_level(logging.WARNING)

    sentry_init(debug=True)

    old_stack = list(Hub.current._stack)

    with push_scope():
        push_scope()

    assert Hub.current._stack == old_stack

    (record,) = (x for x in caplog.records if x.levelname == "WARNING")

    assert record.message.startswith("Leaked 1 scopes:")


def test_scope_popped_too_soon(sentry_init, caplog):
    caplog.set_level(logging.ERROR)

    sentry_init(debug=True)

    old_stack = list(Hub.current._stack)

    with push_scope():
        Hub.current.pop_scope_unsafe()

    assert Hub.current._stack == old_stack

    (record,) = (x for x in caplog.records if x.levelname == "ERROR")

    assert record.message == ("Scope popped too soon. Popped 1 scopes too many.")


def test_scope_event_processor_order(sentry_init, capture_events):
    def before_send(event, hint):
        event["message"] += "baz"
        return event

    sentry_init(debug=True, before_send=before_send)
    events = capture_events()

    with push_scope() as scope:

        @scope.add_event_processor
        def foo(event, hint):
            event["message"] += "foo"
            return event

        with push_scope() as scope:

            @scope.add_event_processor
            def bar(event, hint):
                event["message"] += "bar"
                return event

            capture_message("hi")

    (event,) = events

    assert event["message"] == "hifoobarbaz"


def test_capture_event_with_scope_kwargs(sentry_init, capture_events):
    sentry_init(debug=True)
    events = capture_events()
    capture_event({}, level="info", extras={"foo": "bar"})
    (event,) = events
    assert event["level"] == "info"
    assert event["extra"]["foo"] == "bar"


def test_dedupe_event_processor_drop_records_client_report(
    sentry_init, capture_events, capture_client_reports
):
    """
    DedupeIntegration internally has an event_processor that filters duplicate exceptions.
    We want a duplicate exception to be captured only once and the drop being recorded as
    a client report.
    """
    sentry_init()
    events = capture_events()
    reports = capture_client_reports()

    try:
        raise ValueError("aha!")
    except Exception:
        try:
            capture_exception()
            reraise(*sys.exc_info())
        except Exception:
            capture_exception()

    (event,) = events
    (report,) = reports

    assert event["level"] == "error"
    assert "exception" in event
    assert report == ("event_processor", "error")


def test_event_processor_drop_records_client_report(
    sentry_init, capture_events, capture_client_reports
):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()
    reports = capture_client_reports()

    global global_event_processors

    @add_global_event_processor
    def foo(event, hint):
        return None

    capture_message("dropped")

    with start_transaction(name="dropped"):
        pass

    assert len(events) == 0
    assert reports == [("event_processor", "error"), ("event_processor", "transaction")]

    global_event_processors.pop()
