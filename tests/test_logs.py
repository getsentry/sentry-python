import sys
from unittest import mock
import pytest

import sentry_sdk
from sentry_sdk import _experimental_logger as sentry_logger


minimum_python_37 = pytest.mark.skipif(
    sys.version_info < (3, 7), reason="Asyncio tests need Python >= 3.7"
)


@minimum_python_37
def test_logs_disabled_by_default(sentry_init, capture_envelopes):
    sentry_init()
    envelopes = capture_envelopes()

    sentry_logger.trace("This is a 'trace' log.")
    sentry_logger.debug("This is a 'debug' log...")
    sentry_logger.info("This is a 'info' log...")
    sentry_logger.warn("This is a 'warn' log...")
    sentry_logger.error("This is a 'error' log...")
    sentry_logger.fatal("This is a 'fatal' log...")

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
        assert list(record.keys()) == [
            "severity_text",
            "severity_number",
            "body",
            "attributes",
            "time_unix_nano",
            "trace_id",
        ]

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

    assert log_item["attributes"][1] == {
        "key": "attr_int",
        "value": {"intValue": "1"},
    }  # TODO: this is strange.
    assert log_item["attributes"][2] == {
        "key": "attr_float",
        "value": {"doubleValue": 2.0},
    }
    assert log_item["attributes"][3] == {
        "key": "attr_bool",
        "value": {"boolValue": True},
    }
    assert log_item["attributes"][4] == {
        "key": "attr_string",
        "value": {"stringValue": "string attribute"},
    }
    assert log_item["attributes"][5] == {
        "key": "sentry.environment",
        "value": {"stringValue": "production"},
    }
    assert log_item["attributes"][6] == {
        "key": "sentry.release",
        "value": {"stringValue": mock.ANY},
    }
    assert log_item["attributes"][7] == {
        "key": "sentry.message.parameters.my_var",
        "value": {"stringValue": "some value"},
    }


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
    assert envelopes[0].items[0].payload.json["attributes"][-1] == {
        "key": "sentry.message.parameters.int_var",
        "value": {"intValue": "1"},
    }  # TODO: this is strange.

    assert (
        envelopes[1].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was '2.0'"
    )
    assert envelopes[1].items[0].payload.json["attributes"][-1] == {
        "key": "sentry.message.parameters.float_var",
        "value": {"doubleValue": 2.0},
    }

    assert (
        envelopes[2].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was 'False'"
    )
    assert envelopes[2].items[0].payload.json["attributes"][-1] == {
        "key": "sentry.message.parameters.bool_var",
        "value": {"boolValue": False},
    }

    assert (
        envelopes[3].items[0].payload.json["body"]["stringValue"]
        == "The recorded value was 'some string value'"
    )
    assert envelopes[3].items[0].payload.json["attributes"][-1] == {
        "key": "sentry.message.parameters.string_var",
        "value": {"stringValue": "some string value"},
    }


@minimum_python_37
def test_logs_tied_to_root_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to root spans.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(name="test-root-span") as root_span:
        sentry_logger.warn("This is a log tied to a root span.")

    log_entry = envelopes[0].items[0].payload.json
    assert log_entry["attributes"][-1] == {
        "key": "sentry.trace.parent_span_id",
        "value": {"stringValue": root_span.span_id},
    }


@minimum_python_37
def test_logs_tied_to_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to spans.
    """
    sentry_init(_experiments={"enable_sentry_logs": True})
    envelopes = capture_envelopes()

    with sentry_sdk.start_span(name="test-root-span"):
        with sentry_sdk.start_span(description="test-span") as span:
            sentry_logger.warn("This is a log tied to a span")

    log_entry = envelopes[0].items[0].payload.json
    assert log_entry["attributes"][-1] == {
        "key": "sentry.trace.parent_span_id",
        "value": {"stringValue": span.span_id},
    }
