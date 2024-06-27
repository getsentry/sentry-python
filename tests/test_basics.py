import logging
import os
import sys
import time

import pytest
from sentry_sdk.client import Client

from tests.conftest import patch_start_tracing_child

import sentry_sdk
from sentry_sdk import (
    push_scope,
    configure_scope,
    capture_event,
    capture_exception,
    capture_message,
    start_transaction,
    last_event_id,
    add_breadcrumb,
    isolation_scope,
    Hub,
    Scope,
)
from sentry_sdk.integrations import (
    _AUTO_ENABLING_INTEGRATIONS,
    Integration,
    setup_integrations,
)
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.scope import (  # noqa: F401
    add_global_event_processor,
    global_event_processors,
)
from sentry_sdk.utils import get_sdk_name, reraise
from sentry_sdk.tracing_utils import has_tracing_enabled


def _redis_installed():  # type: () -> bool
    """
    Determines whether Redis is installed.
    """
    try:
        import redis  # noqa: F401
    except ImportError:
        return False

    return True


class NoOpIntegration(Integration):
    """
    A simple no-op integration for testing purposes.
    """

    identifier = "noop"

    @staticmethod
    def setup_once():  # type: () -> None
        pass

    def __eq__(self, __value):  # type: (object) -> bool
        """
        All instances of NoOpIntegration should be considered equal to each other.
        """
        return type(__value) == type(self)


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
    redis_index = _AUTO_ENABLING_INTEGRATIONS.index(
        "sentry_sdk.integrations.redis.RedisIntegration"
    )  # noqa: N806

    sentry_init(auto_enabling_integrations=True, debug=True)

    for import_string in _AUTO_ENABLING_INTEGRATIONS:
        # Ignore redis in the test case, because it does not raise a DidNotEnable
        # exception on import; rather, it raises the exception upon enabling.
        if _AUTO_ENABLING_INTEGRATIONS[redis_index] == import_string:
            continue

        assert any(
            record.message.startswith(
                "Did not import default integration {}:".format(import_string)
            )
            for record in caplog.records
        ), "Problem with checking auto enabling {}".format(import_string)


def test_generic_mechanism(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    try:
        raise ValueError("aha!")
    except Exception:
        capture_exception()

    (event,) = events
    assert event["exception"]["values"][0]["mechanism"]["type"] == "generic"
    assert event["exception"]["values"][0]["mechanism"]["handled"]


def test_option_before_send(sentry_init, capture_events):
    def before_send(event, hint):
        event["extra"] = {"before_send_called": True}
        return event

    def do_this():
        try:
            raise ValueError("aha!")
        except Exception:
            capture_exception()

    sentry_init(before_send=before_send)
    events = capture_events()

    do_this()

    (event,) = events
    assert event["extra"] == {"before_send_called": True}


def test_option_before_send_discard(sentry_init, capture_events):
    def before_send_discard(event, hint):
        return None

    def do_this():
        try:
            raise ValueError("aha!")
        except Exception:
            capture_exception()

    sentry_init(before_send=before_send_discard)
    events = capture_events()

    do_this()

    assert len(events) == 0


def test_option_before_send_transaction(sentry_init, capture_events):
    def before_send_transaction(event, hint):
        assert event["type"] == "transaction"
        event["extra"] = {"before_send_transaction_called": True}
        return event

    sentry_init(
        before_send_transaction=before_send_transaction,
        traces_sample_rate=1.0,
    )
    events = capture_events()
    transaction = start_transaction(name="foo")
    transaction.finish()

    (event,) = events
    assert event["transaction"] == "foo"
    assert event["extra"] == {"before_send_transaction_called": True}


def test_option_before_send_transaction_discard(sentry_init, capture_events):
    def before_send_transaction_discard(event, hint):
        return None

    sentry_init(
        before_send_transaction=before_send_transaction_discard,
        traces_sample_rate=1.0,
    )
    events = capture_events()
    transaction = start_transaction(name="foo")
    transaction.finish()

    assert len(events) == 0


def test_option_before_breadcrumb(sentry_init, capture_events, monkeypatch):
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
        sentry_sdk.get_client().transport, "record_lost_event", record_lost_event
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


@pytest.mark.parametrize(
    "enable_tracing, traces_sample_rate, tracing_enabled, updated_traces_sample_rate",
    [
        (None, None, False, None),
        (False, 0.0, False, 0.0),
        (False, 1.0, False, 1.0),
        (None, 1.0, True, 1.0),
        (True, 1.0, True, 1.0),
        (None, 0.0, True, 0.0),  # We use this as - it's configured but turned off
        (True, 0.0, True, 0.0),  # We use this as - it's configured but turned off
        (True, None, True, 1.0),
    ],
)
def test_option_enable_tracing(
    sentry_init,
    enable_tracing,
    traces_sample_rate,
    tracing_enabled,
    updated_traces_sample_rate,
):
    sentry_init(enable_tracing=enable_tracing, traces_sample_rate=traces_sample_rate)
    options = sentry_sdk.get_client().options
    assert has_tracing_enabled(options) is tracing_enabled
    assert options["traces_sample_rate"] == updated_traces_sample_rate


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
    """
    This test can be removed when we remove push_scope and the Hub from the SDK.
    """
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


@pytest.mark.skip(
    reason="This test is not valid anymore, because push_scope just returns the isolation scope. This test should be removed once the Hub is removed"
)
@pytest.mark.parametrize("null_client", (True, False))
def test_push_scope_callback(sentry_init, null_client, capture_events):
    """
    This test can be removed when we remove push_scope and the Hub from the SDK.
    """
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

    Scope.get_isolation_scope().clear()

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


@pytest.mark.skip(
    reason="This test is not valid anymore, because with the new Scopes calling bind_client on the Hub sets the client on the global scope. This test should be removed once the Hub is removed"
)
def test_client_initialized_within_scope(sentry_init, caplog):
    """
    This test can be removed when we remove push_scope and the Hub from the SDK.
    """
    caplog.set_level(logging.WARNING)

    sentry_init()

    with push_scope():
        Hub.current.bind_client(Client())

    (record,) = (x for x in caplog.records if x.levelname == "WARNING")

    assert record.msg.startswith("init() called inside of pushed scope.")


@pytest.mark.skip(
    reason="This test is not valid anymore, because with the new Scopes the push_scope just returns the isolation scope. This test should be removed once the Hub is removed"
)
def test_scope_leaks_cleaned_up(sentry_init, caplog):
    """
    This test can be removed when we remove push_scope and the Hub from the SDK.
    """
    caplog.set_level(logging.WARNING)

    sentry_init()

    old_stack = list(Hub.current._stack)

    with push_scope():
        push_scope()

    assert Hub.current._stack == old_stack

    (record,) = (x for x in caplog.records if x.levelname == "WARNING")

    assert record.message.startswith("Leaked 1 scopes:")


@pytest.mark.skip(
    reason="This test is not valid anymore, because with the new Scopes there is not pushing and popping of scopes. This test should be removed once the Hub is removed"
)
def test_scope_popped_too_soon(sentry_init, caplog):
    """
    This test can be removed when we remove push_scope and the Hub from the SDK.
    """
    caplog.set_level(logging.ERROR)

    sentry_init()

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
    sentry_init()
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


@pytest.mark.parametrize(
    "installed_integrations, expected_name",
    [
        # integrations with own name
        (["django"], "sentry.python.django"),
        (["flask"], "sentry.python.flask"),
        (["fastapi"], "sentry.python.fastapi"),
        (["bottle"], "sentry.python.bottle"),
        (["falcon"], "sentry.python.falcon"),
        (["quart"], "sentry.python.quart"),
        (["sanic"], "sentry.python.sanic"),
        (["starlette"], "sentry.python.starlette"),
        (["chalice"], "sentry.python.chalice"),
        (["serverless"], "sentry.python.serverless"),
        (["pyramid"], "sentry.python.pyramid"),
        (["tornado"], "sentry.python.tornado"),
        (["aiohttp"], "sentry.python.aiohttp"),
        (["aws_lambda"], "sentry.python.aws_lambda"),
        (["gcp"], "sentry.python.gcp"),
        (["beam"], "sentry.python.beam"),
        (["asgi"], "sentry.python.asgi"),
        (["wsgi"], "sentry.python.wsgi"),
        # integrations without name
        (["argv"], "sentry.python"),
        (["atexit"], "sentry.python"),
        (["boto3"], "sentry.python"),
        (["celery"], "sentry.python"),
        (["dedupe"], "sentry.python"),
        (["excepthook"], "sentry.python"),
        (["executing"], "sentry.python"),
        (["modules"], "sentry.python"),
        (["pure_eval"], "sentry.python"),
        (["redis"], "sentry.python"),
        (["rq"], "sentry.python"),
        (["sqlalchemy"], "sentry.python"),
        (["stdlib"], "sentry.python"),
        (["threading"], "sentry.python"),
        (["trytond"], "sentry.python"),
        (["logging"], "sentry.python"),
        (["gnu_backtrace"], "sentry.python"),
        (["httpx"], "sentry.python"),
        # precedence of frameworks
        (["flask", "django", "celery"], "sentry.python.django"),
        (["fastapi", "flask", "redis"], "sentry.python.flask"),
        (["bottle", "fastapi", "httpx"], "sentry.python.fastapi"),
        (["falcon", "bottle", "logging"], "sentry.python.bottle"),
        (["quart", "falcon", "gnu_backtrace"], "sentry.python.falcon"),
        (["sanic", "quart", "sqlalchemy"], "sentry.python.quart"),
        (["starlette", "sanic", "rq"], "sentry.python.sanic"),
        (["chalice", "starlette", "modules"], "sentry.python.starlette"),
        (["serverless", "chalice", "pure_eval"], "sentry.python.chalice"),
        (["pyramid", "serverless", "modules"], "sentry.python.serverless"),
        (["tornado", "pyramid", "executing"], "sentry.python.pyramid"),
        (["aiohttp", "tornado", "dedupe"], "sentry.python.tornado"),
        (["aws_lambda", "aiohttp", "boto3"], "sentry.python.aiohttp"),
        (["gcp", "aws_lambda", "atexit"], "sentry.python.aws_lambda"),
        (["beam", "gcp", "argv"], "sentry.python.gcp"),
        (["asgi", "beam", "stdtlib"], "sentry.python.beam"),
        (["wsgi", "asgi", "boto3"], "sentry.python.asgi"),
        (["wsgi", "celery", "redis"], "sentry.python.wsgi"),
    ],
)
def test_get_sdk_name(installed_integrations, expected_name):
    assert get_sdk_name(installed_integrations) == expected_name


def _hello_world(word):
    return "Hello, {}".format(word)


def test_functions_to_trace(sentry_init, capture_events):
    functions_to_trace = [
        {"qualified_name": "tests.test_basics._hello_world"},
        {"qualified_name": "time.sleep"},
    ]

    sentry_init(
        traces_sample_rate=1.0,
        functions_to_trace=functions_to_trace,
    )

    events = capture_events()

    with start_transaction(name="something"):
        time.sleep(0)

        for word in ["World", "You"]:
            _hello_world(word)

    assert len(events) == 1

    (event,) = events

    assert len(event["spans"]) == 3
    assert event["spans"][0]["description"] == "time.sleep"
    assert event["spans"][1]["description"] == "tests.test_basics._hello_world"
    assert event["spans"][2]["description"] == "tests.test_basics._hello_world"


class WorldGreeter:
    def __init__(self, word):
        self.word = word

    def greet(self, new_word=None):
        return "Hello, {}".format(new_word if new_word else self.word)


def test_functions_to_trace_with_class(sentry_init, capture_events):
    functions_to_trace = [
        {"qualified_name": "tests.test_basics.WorldGreeter.greet"},
    ]

    sentry_init(
        traces_sample_rate=1.0,
        functions_to_trace=functions_to_trace,
    )

    events = capture_events()

    with start_transaction(name="something"):
        wg = WorldGreeter("World")
        wg.greet()
        wg.greet("You")

    assert len(events) == 1

    (event,) = events

    assert len(event["spans"]) == 2
    assert event["spans"][0]["description"] == "tests.test_basics.WorldGreeter.greet"
    assert event["spans"][1]["description"] == "tests.test_basics.WorldGreeter.greet"


@pytest.mark.skipif(_redis_installed(), reason="skipping because redis is installed")
def test_redis_disabled_when_not_installed(sentry_init):
    sentry_init()

    assert sentry_sdk.get_client().get_integration(RedisIntegration) is None


def test_multiple_setup_integrations_calls():
    first_call_return = setup_integrations([NoOpIntegration()], with_defaults=False)
    assert first_call_return == {NoOpIntegration.identifier: NoOpIntegration()}

    second_call_return = setup_integrations([NoOpIntegration()], with_defaults=False)
    assert second_call_return == {NoOpIntegration.identifier: NoOpIntegration()}


class TracingTestClass:
    @staticmethod
    def static(arg):
        return arg

    @classmethod
    def class_(cls, arg):
        return cls, arg


def test_staticmethod_tracing(sentry_init):
    test_staticmethod_name = "tests.test_basics.TracingTestClass.static"

    assert (
        ".".join(
            [
                TracingTestClass.static.__module__,
                TracingTestClass.static.__qualname__,
            ]
        )
        == test_staticmethod_name
    ), "The test static method was moved or renamed. Please update the name accordingly"

    sentry_init(functions_to_trace=[{"qualified_name": test_staticmethod_name}])

    for instance_or_class in (TracingTestClass, TracingTestClass()):
        with patch_start_tracing_child() as fake_start_child:
            assert instance_or_class.static(1) == 1
            assert fake_start_child.call_count == 1


def test_classmethod_tracing(sentry_init):
    test_classmethod_name = "tests.test_basics.TracingTestClass.class_"

    assert (
        ".".join(
            [
                TracingTestClass.class_.__module__,
                TracingTestClass.class_.__qualname__,
            ]
        )
        == test_classmethod_name
    ), "The test class method was moved or renamed. Please update the name accordingly"

    sentry_init(functions_to_trace=[{"qualified_name": test_classmethod_name}])

    for instance_or_class in (TracingTestClass, TracingTestClass()):
        with patch_start_tracing_child() as fake_start_child:
            assert instance_or_class.class_(1) == (TracingTestClass, 1)
            assert fake_start_child.call_count == 1


def test_last_event_id(sentry_init):
    sentry_init(enable_tracing=True)

    assert last_event_id() is None

    capture_exception(Exception("test"))

    assert last_event_id() is not None


def test_last_event_id_transaction(sentry_init):
    sentry_init(enable_tracing=True)

    assert last_event_id() is None

    with start_transaction(name="test"):
        pass

    assert last_event_id() is None, "Transaction should not set last_event_id"


def test_last_event_id_scope(sentry_init):
    sentry_init(enable_tracing=True)

    # Should not crash
    with isolation_scope() as scope:
        assert scope.last_event_id() is None
