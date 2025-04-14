import logging
import warnings

import pytest

from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

other_logger = logging.getLogger("testfoo")
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def reset_level():
    other_logger.setLevel(logging.DEBUG)
    logger.setLevel(logging.DEBUG)


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
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"]["values"])


@pytest.mark.parametrize("integrations", [None, [], [LoggingIntegration()]])
@pytest.mark.parametrize(
    "kwargs", [{"exc_info": None}, {}, {"exc_info": 0}, {"exc_info": False}]
)
def test_logging_defaults(integrations, sentry_init, capture_events, kwargs):
    sentry_init(integrations=integrations)
    events = capture_events()

    logger.info("bread")
    logger.critical("LOL", **kwargs)
    (event,) = events

    assert event["level"] == "fatal"
    assert any(crumb["message"] == "bread" for crumb in event["breadcrumbs"]["values"])
    assert not any(
        crumb["message"] == "LOL" for crumb in event["breadcrumbs"]["values"]
    )
    assert "threads" not in event


def test_logging_extra_data(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
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
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
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
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
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
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.setLevel(logging.WARNING)
    logger.error("hi")
    (event,) = events
    assert event["level"] == "error"
    assert event["logentry"]["message"] == "hi"

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
        assert events[0]["logentry"]["params"] == [logging_level]

        del events[:]


def test_logging_filters(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
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
    assert events[0]["logentry"]["params"] == []

    assert events[1]["level"] == "warning"
    assert "UserWarning: second" in events[1]["logentry"]["message"]
    assert events[1]["logentry"]["params"] == []

    # Using recwarn suppresses the "third" warning in the test output
    assert len(recwarn) == 1
    assert str(recwarn[0].message) == "third"


def test_ignore_logger(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    ignore_logger("testfoo")

    other_logger.error("hi")

    assert not events


def test_ignore_logger_wildcard(sentry_init, capture_events):
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    ignore_logger("testfoo.*")

    nested_logger = logging.getLogger("testfoo.submodule")

    logger.error("hi")

    nested_logger.error("bye")

    (event,) = events
    assert event["logentry"]["message"] == "hi"


def test_logging_dictionary_interpolation(sentry_init, capture_events):
    """Here we test an entire dictionary being interpolated into the log message."""
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
    events = capture_events()

    logger.error("this is a log with a dictionary %s", {"foo": "bar"})

    (event,) = events
    assert event["logentry"]["message"] == "this is a log with a dictionary %s"
    assert event["logentry"]["params"] == {"foo": "bar"}


def test_logging_dictionary_args(sentry_init, capture_events):
    """Here we test items from a dictionary being interpolated into the log message."""
    sentry_init(integrations=[LoggingIntegration()], default_integrations=False)
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
    assert event["logentry"]["params"] == {"foo": "bar", "bar": "baz"}
