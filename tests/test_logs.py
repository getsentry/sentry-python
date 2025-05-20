import json
import logging
import sys
import time
from typing import List, Any, Mapping, Union
import pytest

import sentry_sdk
import sentry_sdk.logger
from sentry_sdk import get_client
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.types import Log
from sentry_sdk.consts import SPANDATA, VERSION

minimum_python_37 = pytest.mark.skipif(
    sys.version_info < (3, 7), reason="Asyncio tests need Python >= 3.7"
)


def otel_attributes_to_dict(otel_attrs):
    # type: (Mapping[str, Any]) -> Mapping[str, Any]
    def _convert_attr(attr):
        # type: (Mapping[str, Union[str, float, bool]]) -> Any
        if attr["type"] == "boolean":
            return attr["value"]
        if attr["type"] == "double":
            return attr["value"]
        if attr["type"] == "integer":
            return attr["value"]
        if attr["value"].startswith("{"):
            try:
                return json.loads(attr["value"])
            except ValueError:
                pass
        return str(attr["value"])

    return {k: _convert_attr(v) for (k, v) in otel_attrs.items()}


def envelopes_to_logs(envelopes: List[Envelope]) -> List[Log]:
    res = []  # type: List[Log]
    for envelope in envelopes:
        for item in envelope.items:
            if item.type == "log":
                for log_json in item.payload.json["items"]:
                    log = {
                        "severity_text": log_json["attributes"]["sentry.severity_text"][
                            "value"
                        ],
                        "severity_number": int(
                            log_json["attributes"]["sentry.severity_number"]["value"]
                        ),
                        "body": log_json["body"],
                        "attributes": otel_attributes_to_dict(log_json["attributes"]),
                        "time_unix_nano": int(float(log_json["timestamp"]) * 1e9),
                        "trace_id": log_json["trace_id"],
                    }  # type: Log
                    res.append(log)
    return res


@minimum_python_37
def test_logs_disabled_by_default(sentry_init, capture_envelopes):
    sentry_init()

    python_logger = logging.Logger("some-logger")

    envelopes = capture_envelopes()

    sentry_sdk.logger.trace("This is a 'trace' log.")
    sentry_sdk.logger.debug("This is a 'debug' log...")
    sentry_sdk.logger.info("This is a 'info' log...")
    sentry_sdk.logger.warning("This is a 'warning' log...")
    sentry_sdk.logger.error("This is a 'error' log...")
    sentry_sdk.logger.fatal("This is a 'fatal' log...")
    python_logger.warning("sad")

    assert len(envelopes) == 0


@minimum_python_37
def test_logs_basics(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    sentry_sdk.logger.trace("This is a 'trace' log...")
    sentry_sdk.logger.debug("This is a 'debug' log...")
    sentry_sdk.logger.info("This is a 'info' log...")
    sentry_sdk.logger.warning("This is a 'warn' log...")
    sentry_sdk.logger.error("This is a 'error' log...")
    sentry_sdk.logger.fatal("This is a 'fatal' log...")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0].get("severity_text") == "trace"
    assert logs[0].get("severity_number") == 1

    assert logs[1].get("severity_text") == "debug"
    assert logs[1].get("severity_number") == 5

    assert logs[2].get("severity_text") == "info"
    assert logs[2].get("severity_number") == 9

    assert logs[3].get("severity_text") == "warn"
    assert logs[3].get("severity_number") == 13

    assert logs[4].get("severity_text") == "error"
    assert logs[4].get("severity_number") == 17

    assert logs[5].get("severity_text") == "fatal"
    assert logs[5].get("severity_number") == 21


@minimum_python_37
def test_logs_before_send_log(sentry_init, capture_envelopes):
    before_log_called = [False]

    def _before_log(record, hint):
        assert set(record.keys()) == {
            "severity_text",
            "severity_number",
            "body",
            "attributes",
            "time_unix_nano",
            "trace_id",
        }

        if record["severity_text"] in ["fatal", "error"]:
            return None

        before_log_called[0] = True

        return record

    sentry_init(
        _experiments={
            "enable_logs": True,
            "before_send_log": _before_log,
        }
    )
    envelopes = capture_envelopes()

    sentry_sdk.logger.trace("This is a 'trace' log...")
    sentry_sdk.logger.debug("This is a 'debug' log...")
    sentry_sdk.logger.info("This is a 'info' log...")
    sentry_sdk.logger.warning("This is a 'warning' log...")
    sentry_sdk.logger.error("This is a 'error' log...")
    sentry_sdk.logger.fatal("This is a 'fatal' log...")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 4

    assert logs[0]["severity_text"] == "trace"
    assert logs[1]["severity_text"] == "debug"
    assert logs[2]["severity_text"] == "info"
    assert logs[3]["severity_text"] == "warn"
    assert before_log_called[0]


@minimum_python_37
def test_logs_attributes(sentry_init, capture_envelopes):
    """
    Passing arbitrary attributes to log messages.
    """
    sentry_init(_experiments={"enable_logs": True}, server_name="test-server")
    envelopes = capture_envelopes()

    attrs = {
        "attr_int": 1,
        "attr_float": 2.0,
        "attr_bool": True,
        "attr_string": "string attribute",
    }

    sentry_sdk.logger.warning(
        "The recorded value was '{my_var}'", my_var="some value", attributes=attrs
    )

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0]["body"] == "The recorded value was 'some value'"

    for k, v in attrs.items():
        assert logs[0]["attributes"][k] == v
    assert logs[0]["attributes"]["sentry.environment"] == "production"
    assert "sentry.release" in logs[0]["attributes"]
    assert logs[0]["attributes"]["sentry.message.parameter.my_var"] == "some value"
    assert logs[0]["attributes"][SPANDATA.SERVER_ADDRESS] == "test-server"
    assert logs[0]["attributes"]["sentry.sdk.name"].startswith("sentry.python")
    assert logs[0]["attributes"]["sentry.sdk.version"] == VERSION


@minimum_python_37
def test_logs_message_params(sentry_init, capture_envelopes):
    """
    This is the official way of how to pass vars to log messages.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    sentry_sdk.logger.warning("The recorded value was '{int_var}'", int_var=1)
    sentry_sdk.logger.warning("The recorded value was '{float_var}'", float_var=2.0)
    sentry_sdk.logger.warning("The recorded value was '{bool_var}'", bool_var=False)
    sentry_sdk.logger.warning(
        "The recorded value was '{string_var}'", string_var="some string value"
    )
    sentry_sdk.logger.error(
        "The recorded error was '{error}'", error=Exception("some error")
    )

    get_client().flush()
    logs = envelopes_to_logs(envelopes)

    assert logs[0]["body"] == "The recorded value was '1'"
    assert logs[0]["attributes"]["sentry.message.parameter.int_var"] == 1

    assert logs[1]["body"] == "The recorded value was '2.0'"
    assert logs[1]["attributes"]["sentry.message.parameter.float_var"] == 2.0

    assert logs[2]["body"] == "The recorded value was 'False'"
    assert logs[2]["attributes"]["sentry.message.parameter.bool_var"] is False

    assert logs[3]["body"] == "The recorded value was 'some string value'"
    assert (
        logs[3]["attributes"]["sentry.message.parameter.string_var"]
        == "some string value"
    )

    assert logs[4]["body"] == "The recorded error was 'some error'"
    assert (
        logs[4]["attributes"]["sentry.message.parameter.error"]
        == "Exception('some error')"
    )


@minimum_python_37
def test_logs_tied_to_root_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to root spans.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(name="test-root-span") as trx:
        sentry_sdk.logger.warning("This is a log tied to a root span")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0]["attributes"]["sentry.trace.parent_span_id"] == trx.span_id


@minimum_python_37
def test_logs_tied_to_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to spans.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(name="test-root-span"):
        with sentry_sdk.start_span(name="test-span") as span:
            sentry_sdk.logger.warning("This is a log tied to a child span")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0]["attributes"]["sentry.trace.parent_span_id"] == span.span_id


@minimum_python_37
def test_logger_integration_warning(sentry_init, capture_envelopes):
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


@minimum_python_37
def test_logger_integration_debug(sentry_init, capture_envelopes):
    """
    The python logger module should not create 'debug' sentry logs if the flag is on by default
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.debug("this is %s a template %s", "1", "2")
    get_client().flush()

    assert len(envelopes) == 0


@minimum_python_37
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


@minimum_python_37
def test_logging_errors(sentry_init, capture_envelopes):
    """
    The python logger module should be able to log errors without erroring
    """
    sentry_init(
        _experiments={"enable_logs": True},
        integrations=[LoggingIntegration(event_level="ERROR")],
    )
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.error(Exception("test exc 1"))
    python_logger.error("error is %s", Exception("test exc 2"))
    get_client().flush()

    error_event_1 = envelopes[0].items[0].payload.json
    assert error_event_1["level"] == "error"
    error_event_2 = envelopes[1].items[0].payload.json
    assert error_event_2["level"] == "error"

    logs = envelopes_to_logs(envelopes)
    assert logs[0]["severity_text"] == "error"
    assert "sentry.message.template" not in logs[0]["attributes"]
    assert "sentry.message.parameter.0" not in logs[0]["attributes"]
    assert "code.line.number" in logs[0]["attributes"]

    assert logs[1]["severity_text"] == "error"
    assert logs[1]["attributes"]["sentry.message.template"] == "error is %s"
    assert (
        logs[1]["attributes"]["sentry.message.parameter.0"] == "Exception('test exc 2')"
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


def test_auto_flush_logs_after_100(sentry_init, capture_envelopes):
    """
    If you log >100 logs, it should automatically trigger a flush.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    for i in range(200):
        python_logger.warning("log #%d", i)

    for _ in range(500):
        time.sleep(1.0 / 100.0)
        if len(envelopes) > 0:
            return

    raise AssertionError("200 logs were never flushed after five seconds")


@minimum_python_37
def test_auto_flush_logs_after_5s(sentry_init, capture_envelopes):
    """
    If you log a single log, it should automatically flush after 5 seconds, at most 10 seconds.
    """
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning("log #%d", 1)

    for _ in range(100):
        time.sleep(1.0 / 10.0)
        if len(envelopes) > 0:
            return

    raise AssertionError("1 logs was never flushed after 10 seconds")
