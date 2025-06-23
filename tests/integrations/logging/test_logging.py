import logging
import warnings

import pytest

from sentry_sdk import get_client
from sentry_sdk.consts import VERSION
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from tests.test_logs import envelopes_to_logs

other_logger = logging.getLogger("testfoo")
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def reset_level():
    other_logger.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)


@pytest.mark.parametrize("integrations", [None, [], [LoggingIntegration()]])
@pytest.mark.parametrize(
    "kwargs", [{"exc_info": None}, {}, {"exc_info": 0}, {"exc_info": False}]
)
def test_logging_defaults(integrations, sentry_init, capture_events, kwargs):
    sentry_init(integrations=integrations)
    events = capture_events()

    logger.info("bread")
    logger.error("error")
    logger.critical("LOL", **kwargs)

    assert len(events) == 0


@pytest.mark.parametrize(
    "kwargs", [{"exc_info": None}, {}, {"exc_info": 0}, {"exc_info": False}]
)
def test_logging_basic(sentry_init, capture_events, kwargs):
    sentry_init(integrations=[LoggingIntegration(event_level=logging.ERROR)])
    events = capture_events()

    logger.info("bread")
    logger.error("error")
    logger.critical("LOL", **kwargs)
    (error_event, critical_event) = events

    assert error_event["level"] == "error"
    assert any(
        crumb["message"] == "bread" for crumb in error_event["breadcrumbs"]["values"]
    )
    assert not any(
        crumb["message"] == "LOL" for crumb in error_event["breadcrumbs"]["values"]
    )
    assert "threads" not in error_event

    assert critical_event["level"] == "fatal"
    assert any(
        crumb["message"] == "bread" for crumb in critical_event["breadcrumbs"]["values"]
    )
    assert not any(
        crumb["message"] == "LOL" for crumb in critical_event["breadcrumbs"]["values"]
    )
    assert "threads" not in critical_event


@pytest.mark.parametrize("logger", [logger, other_logger])
def test_logging_works_with_many_loggers(sentry_init, capture_events, logger):
    sentry_init(integrations=[LoggingIntegration(event_level="ERROR")])
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL")
    (event,) = events
    assert event["level"] == "fatal"
    assert not event["logentry"]["params"]
    assert event["logentry"]["message"] == "LOL"
    assert event["logentry"]["formatted"] == "LOL"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"]["values"])


def test_logging_extra_data(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.info("bread", extra=dict(foo=42))
    logger.critical("lol", extra=dict(bar=69))

    (event,) = events

    assert event["level"] == "fatal"
    assert event["extra"] == {"bar": 69}
    assert any(
        crumb["message"] == "bread" and crumb["data"] == {"foo": 42}
        for crumb in event["breadcrumbs"]["values"]
    )


def test_logging_extra_data_integer_keys(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.critical("integer in extra keys", extra={1: 1})

    (event,) = events

    assert event["extra"] == {"1": 1}


@pytest.mark.parametrize(
    "enable_stack_trace_kwarg",
    (
        pytest.param({"exc_info": True}, id="exc_info"),
        pytest.param({"stack_info": True}, id="stack_info"),
    ),
)
def test_logging_stack_trace(sentry_init, capture_events, enable_stack_trace_kwarg):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.error("first", **enable_stack_trace_kwarg)
    logger.error("second")

    (
        event_with,
        event_without,
    ) = events

    assert event_with["level"] == "error"
    assert event_with["threads"]["values"][0]["stacktrace"]["frames"]

    assert event_without["level"] == "error"
    assert "threads" not in event_without


def test_logging_level(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.setLevel(logging.WARNING)
    logger.error("hi")
    (event,) = events
    assert event["level"] == "error"
    assert event["logentry"]["message"] == "hi"
    assert event["logentry"]["formatted"] == "hi"

    del events[:]

    logger.setLevel(logging.ERROR)
    logger.warning("hi")
    assert not events


def test_custom_log_level_names(sentry_init, capture_events):
    levels = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARN: "warning",
        logging.WARNING: "warning",
        logging.ERROR: "error",
        logging.CRITICAL: "fatal",
        logging.FATAL: "fatal",
    }

    # set custom log level names
    logging.addLevelName(logging.DEBUG, "custom level debüg: ")
    logging.addLevelName(logging.INFO, "")
    logging.addLevelName(logging.WARN, "custom level warn: ")
    logging.addLevelName(logging.WARNING, "custom level warning: ")
    logging.addLevelName(logging.ERROR, None)
    logging.addLevelName(logging.CRITICAL, "custom level critical: ")
    logging.addLevelName(logging.FATAL, "custom level 🔥: ")

    for logging_level, sentry_level in levels.items():
        logger.setLevel(logging_level)
        sentry_init(
            integrations=[LoggingIntegration(event_level=logging_level)],
            default_integrations=False,
        )
        events = capture_events()

        logger.log(logging_level, "Trying level %s", logging_level)
        assert events
        assert events[0]["level"] == sentry_level
        assert events[0]["logentry"]["message"] == "Trying level %s"
        assert events[0]["logentry"]["formatted"] == f"Trying level {logging_level}"
        assert events[0]["logentry"]["params"] == [logging_level]

        del events[:]


def test_logging_filters(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    should_log = False

    class MyFilter(logging.Filter):
        def filter(self, record):
            return should_log

    logger.addFilter(MyFilter())
    logger.error("hi")

    assert not events

    should_log = True
    logger.error("hi")

    (event,) = events
    assert event["logentry"]["message"] == "hi"
    assert event["logentry"]["formatted"] == "hi"


def test_logging_captured_warnings(sentry_init, capture_events, recwarn):
    sentry_init(
        integrations=[LoggingIntegration(event_level="WARNING")],
        default_integrations=False,
    )
    events = capture_events()

    logging.captureWarnings(True)
    warnings.warn("first", stacklevel=2)
    warnings.warn("second", stacklevel=2)
    logging.captureWarnings(False)

    warnings.warn("third", stacklevel=2)

    assert len(events) == 2

    assert events[0]["level"] == "warning"
    # Captured warnings start with the path where the warning was raised
    assert "UserWarning: first" in events[0]["logentry"]["message"]
    assert "UserWarning: first" in events[0]["logentry"]["formatted"]
    # For warnings, the message and formatted message are the same
    assert events[0]["logentry"]["message"] == events[0]["logentry"]["formatted"]
    assert events[0]["logentry"]["params"] == []

    assert events[1]["level"] == "warning"
    assert "UserWarning: second" in events[1]["logentry"]["message"]
    assert "UserWarning: second" in events[1]["logentry"]["formatted"]
    # For warnings, the message and formatted message are the same
    assert events[1]["logentry"]["message"] == events[1]["logentry"]["formatted"]
    assert events[1]["logentry"]["params"] == []

    # Using recwarn suppresses the "third" warning in the test output
    assert len(recwarn) == 1
    assert str(recwarn[0].message) == "third"


def test_ignore_logger(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    ignore_logger("testfoo")

    other_logger.error("hi")

    assert not events


def test_ignore_logger_whitespace_padding(sentry_init, capture_events):
    """Here we test insensitivity to whitespace padding of ignored loggers"""
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    ignore_logger("testfoo")

    padded_logger = logging.getLogger("       testfoo   ")
    padded_logger.error("hi")
    assert not events


def test_ignore_logger_wildcard(sentry_init, capture_events):
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    ignore_logger("testfoo.*")

    nested_logger = logging.getLogger("testfoo.submodule")

    logger.error("hi")

    nested_logger.error("bye")

    (event,) = events
    assert event["logentry"]["message"] == "hi"
    assert event["logentry"]["formatted"] == "hi"


def test_logging_dictionary_interpolation(sentry_init, capture_events):
    """Here we test an entire dictionary being interpolated into the log message."""
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.error("this is a log with a dictionary %s", {"foo": "bar"})

    (event,) = events
    assert event["logentry"]["message"] == "this is a log with a dictionary %s"
    assert (
        event["logentry"]["formatted"]
        == "this is a log with a dictionary {'foo': 'bar'}"
    )
    assert event["logentry"]["params"] == {"foo": "bar"}


def test_logging_dictionary_args(sentry_init, capture_events):
    """Here we test items from a dictionary being interpolated into the log message."""
    sentry_init(
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        default_integrations=False,
    )
    events = capture_events()

    logger.error(
        "the value of foo is %(foo)s, and the value of bar is %(bar)s",
        {"foo": "bar", "bar": "baz"},
    )

    (event,) = events
    assert (
        event["logentry"]["message"]
        == "the value of foo is %(foo)s, and the value of bar is %(bar)s"
    )
    assert (
        event["logentry"]["formatted"]
        == "the value of foo is bar, and the value of bar is baz"
    )
    assert event["logentry"]["params"] == {"foo": "bar", "bar": "baz"}


def test_sentry_logs_warning(sentry_init, capture_envelopes):
    """
    The python logger module should create 'warn' sentry logs if the flag is on.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning("this is %s a template %s", "1", "2")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    attrs = logs[0]["attributes"]
    assert attrs["sentry.message.template"] == "this is %s a template %s"
    assert "code.file.path" in attrs
    assert "code.line.number" in attrs
    assert attrs["logger.name"] == "test-logger"
    assert attrs["sentry.environment"] == "production"
    assert attrs["sentry.message.parameter.0"] == "1"
    assert attrs["sentry.message.parameter.1"] == "2"
    assert attrs["sentry.origin"] == "auto.logger.log"
    assert logs[0]["severity_number"] == 13
    assert logs[0]["severity_text"] == "warn"


def test_sentry_logs_debug(sentry_init, capture_envelopes):
    """
    The python logger module should not create 'debug' sentry logs if the flag is on by default
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.debug("this is %s a template %s", "1", "2")
    get_client().flush()

    assert len(envelopes) == 0


def test_no_log_infinite_loop(sentry_init, capture_envelopes):
    """
    If 'debug' mode is true, and you set a low log level in the logging integration, there should be no infinite loops.
    """
    sentry_init(
        _experiments={"enable_logs": True},
        integrations=[LoggingIntegration(sentry_logs_level=logging.DEBUG)],
        debug=True,
    )
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.debug("this is %s a template %s", "1", "2")
    get_client().flush()

    assert len(envelopes) == 1


def test_logging_errors(sentry_init, capture_envelopes):
    """
    The python logger module should be able to log errors without erroring
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.error(Exception("test exc 1"))
    python_logger.error("error is %s", Exception("test exc 2"))
    get_client().flush()

    for envelope in envelopes:
        for item in envelope.items:
            for subitem in item.payload.json["items"]:
                assert subitem["level"] == "error"

    logs = envelopes_to_logs(envelopes)
    assert logs[0]["severity_text"] == "error"
    assert "sentry.message.template" not in logs[0]["attributes"]
    assert "sentry.message.parameter.0" not in logs[0]["attributes"]
    assert "code.line.number" in logs[0]["attributes"]

    assert logs[1]["severity_text"] == "error"
    assert logs[1]["attributes"]["sentry.message.template"] == "error is %s"
    assert logs[1]["attributes"]["sentry.message.parameter.0"] in (
        "Exception('test exc 2')",
        "Exception('test exc 2',)",  # py3.6
    )
    assert "code.line.number" in logs[1]["attributes"]

    assert len(logs) == 2


def test_log_strips_project_root(sentry_init, capture_envelopes):
    """
    The python logger should strip project roots from the log record path
    """
    sentry_init(
        _experiments={"enable_logs": True},
        project_root="/custom/test",
    )
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.handle(
        logging.LogRecord(
            name="test-logger",
            level=logging.WARN,
            pathname="/custom/test/blah/path.py",
            lineno=123,
            msg="This is a test log with a custom pathname",
            args=(),
            exc_info=None,
        )
    )
    get_client().flush()

    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 1
    attrs = logs[0]["attributes"]
    assert attrs["code.file.path"] == "blah/path.py"


def test_logger_with_all_attributes(sentry_init, capture_envelopes):
    """
    The python logger should be able to log all attributes, including extra data.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning(
        "log #%d",
        1,
        extra={"foo": "bar", "numeric": 42, "more_complex": {"nested": "data"}},
    )
    get_client().flush()

    logs = envelopes_to_logs(envelopes)

    attributes = logs[0]["attributes"]

    assert "process.pid" in attributes
    assert isinstance(attributes["process.pid"], int)
    del attributes["process.pid"]

    assert "sentry.release" in attributes
    assert isinstance(attributes["sentry.release"], str)
    del attributes["sentry.release"]

    assert "server.address" in attributes
    assert isinstance(attributes["server.address"], str)
    del attributes["server.address"]

    assert "thread.id" in attributes
    assert isinstance(attributes["thread.id"], int)
    del attributes["thread.id"]

    assert "code.file.path" in attributes
    assert isinstance(attributes["code.file.path"], str)
    del attributes["code.file.path"]

    assert "code.function.name" in attributes
    assert isinstance(attributes["code.function.name"], str)
    del attributes["code.function.name"]

    assert "code.line.number" in attributes
    assert isinstance(attributes["code.line.number"], int)
    del attributes["code.line.number"]

    assert "process.executable.name" in attributes
    assert isinstance(attributes["process.executable.name"], str)
    del attributes["process.executable.name"]

    assert "thread.name" in attributes
    assert isinstance(attributes["thread.name"], str)
    del attributes["thread.name"]

    assert attributes.pop("sentry.sdk.name").startswith("sentry.python")

    # Assert on the remaining non-dynamic attributes.
    assert attributes == {
        "foo": "bar",
        "numeric": 42,
        "more_complex": "{'nested': 'data'}",
        "logger.name": "test-logger",
        "sentry.origin": "auto.logger.log",
        "sentry.message.template": "log #%d",
        "sentry.message.parameter.0": 1,
        "sentry.environment": "production",
        "sentry.sdk.version": VERSION,
        "sentry.severity_number": 13,
        "sentry.severity_text": "warn",
    }


def test_sentry_logs_named_parameters(sentry_init, capture_envelopes):
    """
    The python logger module should capture named parameters from dictionary arguments in Sentry logs.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.info(
        "%(source)s call completed, %(input_tk)i input tk, %(output_tk)i output tk (model %(model)s, cost $%(cost).4f)",
        {
            "source": "test_source",
            "input_tk": 100,
            "output_tk": 50,
            "model": "gpt-4",
            "cost": 0.0234,
        },
    )

    get_client().flush()
    logs = envelopes_to_logs(envelopes)

    assert len(logs) == 1
    attrs = logs[0]["attributes"]

    # Check that the template is captured
    assert (
        attrs["sentry.message.template"]
        == "%(source)s call completed, %(input_tk)i input tk, %(output_tk)i output tk (model %(model)s, cost $%(cost).4f)"
    )

    # Check that dictionary arguments are captured as named parameters
    assert attrs["sentry.message.parameter.source"] == "test_source"
    assert attrs["sentry.message.parameter.input_tk"] == 100
    assert attrs["sentry.message.parameter.output_tk"] == 50
    assert attrs["sentry.message.parameter.model"] == "gpt-4"
    assert attrs["sentry.message.parameter.cost"] == 0.0234

    # Check other standard attributes
    assert attrs["logger.name"] == "test-logger"
    assert attrs["sentry.origin"] == "auto.logger.log"
    assert logs[0]["severity_number"] == 9  # info level
    assert logs[0]["severity_text"] == "info"


def test_sentry_logs_named_parameters_complex_values(sentry_init, capture_envelopes):
    """
    The python logger module should handle complex values in named parameters using safe_repr.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    complex_object = {"nested": {"data": [1, 2, 3]}, "tuple": (4, 5, 6)}
    python_logger.warning(
        "Processing %(simple)s with %(complex)s data",
        {
            "simple": "simple_value",
            "complex": complex_object,
        },
    )

    get_client().flush()
    logs = envelopes_to_logs(envelopes)

    assert len(logs) == 1
    attrs = logs[0]["attributes"]

    # Check that simple values are kept as-is
    assert attrs["sentry.message.parameter.simple"] == "simple_value"

    # Check that complex values are converted using safe_repr
    assert "sentry.message.parameter.complex" in attrs
    complex_param = attrs["sentry.message.parameter.complex"]
    assert isinstance(complex_param, str)
    assert "nested" in complex_param
    assert "data" in complex_param
