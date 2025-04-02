import logging
import sys
from typing import List, Any
from unittest import mock
import pytest

import sentry_sdk
from sentry_sdk import _experimental_logger as sentry_logger
from sentry_sdk.integrations.logging import LoggingIntegration

minimum_python_37 = pytest.mark.skipif(
    sys.version_info < (3, 7), reason="Asyncio tests need Python >= 3.7"
)


def otel_attributes_to_dict(otel_attrs: List[Any]):
    return {item["key"]: item["value"] for item in otel_attrs}


@minimum_python_37
def test_logs_disabled_by_default(sentry_init, capture_envelopes):
    sentry_init()

    python_logger = logging.Logger("some-logger")

    envelopes = capture_envelopes()

    sentry_logger.trace("This is a 'trace' log.")
    sentry_logger.debug("This is a 'debug' log...")
    sentry_logger.info("This is a 'info' log...")
    sentry_logger.warn("This is a 'warn' log...")
    sentry_logger.error("This is a 'error' log...")
    sentry_logger.fatal("This is a 'fatal' log...")
    python_logger.warning("sad")

    assert len(envelopes) == 0


@minimum_python_37
def test_logs_basics(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    sentry_logger.trace("This is a 'trace' log...")
    sentry_logger.debug("This is a 'debug' log...")
    sentry_logger.info("This is a 'info' log...")
    sentry_logger.warn("This is a 'warn' log...")
    sentry_logger.error("This is a 'error' log...")
    sentry_logger.fatal("This is a 'fatal' log...")

    assert (
        len(envelopes) == 6
    )  # We will batch those log items into a single envelope at some point

    assert envelopes[0].items[0].payload.json["severityText"] == "trace"
    assert envelopes[0].items[0].payload.json["severityNumber"] == 1

    assert envelopes[1].items[0].payload.json["severityText"] == "debug"
    assert envelopes[1].items[0].payload.json["severityNumber"] == 5

    assert envelopes[2].items[0].payload.json["severityText"] == "info"
    assert envelopes[2].items[0].payload.json["severityNumber"] == 9

    assert envelopes[3].items[0].payload.json["severityText"] == "warn"
    assert envelopes[3].items[0].payload.json["severityNumber"] == 13

    assert envelopes[4].items[0].payload.json["severityText"] == "error"
    assert envelopes[4].items[0].payload.json["severityNumber"] == 17

    assert envelopes[5].items[0].payload.json["severityText"] == "fatal"
    assert envelopes[5].items[0].payload.json["severityNumber"] == 21


@minimum_python_37
def test_logs_before_emit_log(sentry_init, capture_envelopes):
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

        return record

    sentry_init(
        _experiments={
            "enable_sentry_logs": True,
            "before_emit_log": _before_log,
        }
    )
    envelopes = capture_envelopes()

    sentry_logger.trace("This is a 'trace' log...")
    sentry_logger.debug("This is a 'debug' log...")
    sentry_logger.info("This is a 'info' log...")
    sentry_logger.warn("This is a 'warn' log...")
    sentry_logger.error("This is a 'error' log...")
    sentry_logger.fatal("This is a 'fatal' log...")

    assert len(envelopes) == 4

    assert envelopes[0].items[0].payload.json["severityText"] == "trace"
    assert envelopes[1].items[0].payload.json["severityText"] == "debug"
    assert envelopes[2].items[0].payload.json["severityText"] == "info"
    assert envelopes[3].items[0].payload.json["severityText"] == "warn"


@minimum_python_37
def test_logs_attributes(sentry_init, capture_envelopes):
    """
    Passing arbitrary attributes to log messages.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    attrs = {
        "attr_int": 1,
        "attr_float": 2.0,
        "attr_bool": True,
        "attr_string": "string attribute",
    }

    sentry_logger.warn(
        "The recorded value was '{my_var}'", my_var="some value", attributes=attrs
    )

    log_item = envelopes[0].items[0].payload.json
    assert log_item["body"]["stringValue"] == "The recorded value was 'some value'"

    attrs = otel_attributes_to_dict(log_item["attributes"])
    assert attrs["attr_int"] == {"intValue": "1"}
    assert attrs["attr_float"] == {"doubleValue": 2.0}
    assert attrs["attr_bool"] == {"boolValue": True}
    assert attrs["attr_string"] == {"stringValue": "string attribute"}
    assert attrs["sentry.environment"] == {"stringValue": "production"}
    assert attrs["sentry.release"] == {"stringValue": mock.ANY}
    assert attrs["sentry.message.parameters.my_var"] == {"stringValue": "some value"}


@minimum_python_37
def test_logs_message_params(sentry_init, capture_envelopes):
    """
    This is the official way of how to pass vars to log messages.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    sentry_logger.warn("The recorded value was '{int_var}'", int_var=1)
    sentry_logger.warn("The recorded value was '{float_var}'", float_var=2.0)
    sentry_logger.warn("The recorded value was '{bool_var}'", bool_var=False)
    sentry_logger.warn(
        "The recorded value was '{string_var}'", string_var="some string value"
    )

    assert (
        envelopes[0].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was '1'"
    )
    assert otel_attributes_to_dict(envelopes[0].items[0].payload.json["attributes"])[
        "sentry.message.parameters.int_var"
    ] == {"intValue": "1"}

    assert (
        envelopes[1].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was '2.0'"
    )
    assert otel_attributes_to_dict(envelopes[1].items[0].payload.json["attributes"])[
        "sentry.message.parameters.float_var"
    ] == {"doubleValue": 2.0}

    assert (
        envelopes[2].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was 'False'"
    )
    assert otel_attributes_to_dict(envelopes[2].items[0].payload.json["attributes"])[
        "sentry.message.parameters.bool_var"
    ] == {"boolValue": False}

    assert (
        envelopes[3].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was 'some string value'"
    )
    assert otel_attributes_to_dict(envelopes[3].items[0].payload.json["attributes"])[
        "sentry.message.parameters.string_var"
    ] == {"stringValue": "some string value"}


@minimum_python_37
def test_logs_tied_to_transactions(sentry_init, capture_envelopes):
    """
    Log messages are also tied to transactions.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(name="test-transaction") as trx:
        sentry_logger.warn("This is a log tied to a transaction")

    log_entry = envelopes[0].items[0].payload.json
    assert log_entry["attributes"][-1] == {
        "key": "sentry.trace.parent_span_id",
        "value": {"stringValue": trx.span_id},
    }


@minimum_python_37
def test_logs_tied_to_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to spans.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(name="test-transaction"):
        with sentry_sdk.start_span(description="test-span") as span:
            sentry_logger.warn("This is a log tied to a span")

    attrs = otel_attributes_to_dict(envelopes[0].items[0].payload.json["attributes"])
    assert attrs["sentry.trace.parent_span_id"] == {"stringValue": span.span_id}


@minimum_python_37
def test_logger_integration_warning(sentry_init, capture_envelopes):
    """
    The python logger module should create 'warn' sentry logs if the flag is on.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning("this is %s a template %s", "1", "2")

    log_entry = envelopes[0].items[0].payload.json
    attrs = otel_attributes_to_dict(log_entry["attributes"])
    assert attrs["sentry.message.template"] == {
        "stringValue": "this is %s a template %s"
    }
    assert "code.file.path" in attrs
    assert "code.line.number" in attrs
    assert attrs["logger.name"] == {"stringValue": "test-logger"}
    assert attrs["sentry.environment"] == {"stringValue": "production"}
    assert attrs["sentry.message.parameters.0"] == {"stringValue": "1"}
    assert attrs["sentry.message.parameters.1"]
    assert log_entry["severityNumber"] == 13
    assert log_entry["severityText"] == "warn"


@minimum_python_37
def test_logger_integration_debug(sentry_init, capture_envelopes):
    """
    The python logger module should not create 'debug' sentry logs if the flag is on by default
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.debug("this is %s a template %s", "1", "2")

    assert len(envelopes) == 0


@minimum_python_37
def test_no_log_infinite_loop(sentry_init, capture_envelopes):
    """
    If 'debug' mode is true, and you set a low log level in the logging integration, there should be no infinite loops.
    """
    sentry_init(
        _experiments={"enable_sentry_logs": True},
        integrations=[LoggingIntegration(sentry_logs_level=logging.DEBUG)],
        debug=True,
    )
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.debug("this is %s a template %s", "1", "2")

    assert len(envelopes) == 1


@minimum_python_37
def test_logging_errors(sentry_init, capture_envelopes):
    """
    The python logger module should be able to log errors without erroring
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.error(Exception("test exc 1"))
    python_logger.error("error is %s", Exception("test exc 2"))

    error_event_1 = envelopes[0].items[0].payload.json
    assert error_event_1["level"] == "error"

    log_event_1 = envelopes[1].items[0].payload.json
    assert log_event_1["severityText"] == "error"
    # If only logging an exception, there is no "sentry.message.template" or "sentry.message.parameters.0"
    assert len(log_event_1["attributes"]) == 10
    assert log_event_1["attributes"][0]["key"] == "code.line.number"

    error_event_2 = envelopes[2].items[0].payload.json
    assert error_event_2["level"] == "error"

    log_event_2 = envelopes[3].items[0].payload.json
    assert log_event_2["severityText"] == "error"
    assert len(log_event_2["attributes"]) == 12
    assert log_event_2["attributes"][0]["key"] == "sentry.message.template"
    assert log_event_2["attributes"][0]["value"] == {"stringValue": "error is %s"}
    assert log_event_2["attributes"][1]["key"] == "sentry.message.parameters.0"
    assert log_event_2["attributes"][1]["value"] == {
        "stringValue": "Exception('test exc 2')"
    }
    assert log_event_2["attributes"][2]["key"] == "code.line.number"

    assert len(envelopes) == 4
