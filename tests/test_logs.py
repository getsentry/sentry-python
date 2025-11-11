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
    sentry_init(enable_logs=True)
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
def test_logs_experimental_option_still_works(sentry_init, capture_envelopes):
    sentry_init(_experiments={"enable_logs": True})
    envelopes = capture_envelopes()

    sentry_sdk.logger.error("This is an error log...")

    get_client().flush()

    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 1

    assert logs[0].get("severity_text") == "error"
    assert logs[0].get("severity_number") == 17


@minimum_python_37
def test_logs_before_send_log(sentry_init, capture_envelopes):
    before_log_called = False

    def _before_log(record, hint):
        nonlocal before_log_called

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

        before_log_called = True

        return record

    sentry_init(
        enable_logs=True,
        before_send_log=_before_log,
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
    assert before_log_called is True


@minimum_python_37
def test_logs_before_send_log_experimental_option_still_works(
    sentry_init, capture_envelopes
):
    before_log_called = False

    def _before_log(record, hint):
        nonlocal before_log_called
        before_log_called = True

        return record

    sentry_init(
        enable_logs=True,
        _experiments={
            "before_send_log": _before_log,
        },
    )
    envelopes = capture_envelopes()

    sentry_sdk.logger.error("This is an error log...")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert len(logs) == 1

    assert logs[0]["severity_text"] == "error"
    assert before_log_called is True


@minimum_python_37
def test_logs_attributes(sentry_init, capture_envelopes):
    """
    Passing arbitrary attributes to log messages.
    """
    sentry_init(enable_logs=True, server_name="test-server")
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
    if sentry_sdk.get_client().options.get("release") is not None:
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
    sentry_init(enable_logs=True)
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
    sentry_sdk.logger.warning("The recorded value was hardcoded.")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)

    assert logs[0]["body"] == "The recorded value was '1'"
    assert logs[0]["attributes"]["sentry.message.parameter.int_var"] == 1
    assert (
        logs[0]["attributes"]["sentry.message.template"]
        == "The recorded value was '{int_var}'"
    )

    assert logs[1]["body"] == "The recorded value was '2.0'"
    assert logs[1]["attributes"]["sentry.message.parameter.float_var"] == 2.0
    assert (
        logs[1]["attributes"]["sentry.message.template"]
        == "The recorded value was '{float_var}'"
    )

    assert logs[2]["body"] == "The recorded value was 'False'"
    assert logs[2]["attributes"]["sentry.message.parameter.bool_var"] is False
    assert (
        logs[2]["attributes"]["sentry.message.template"]
        == "The recorded value was '{bool_var}'"
    )

    assert logs[3]["body"] == "The recorded value was 'some string value'"
    assert (
        logs[3]["attributes"]["sentry.message.parameter.string_var"]
        == "some string value"
    )
    assert (
        logs[3]["attributes"]["sentry.message.template"]
        == "The recorded value was '{string_var}'"
    )

    assert logs[4]["body"] == "The recorded error was 'some error'"
    assert (
        logs[4]["attributes"]["sentry.message.parameter.error"]
        == "Exception('some error')"
    )
    assert (
        logs[4]["attributes"]["sentry.message.template"]
        == "The recorded error was '{error}'"
    )

    assert logs[5]["body"] == "The recorded value was hardcoded."
    assert "sentry.message.template" not in logs[5]["attributes"]


@minimum_python_37
def test_logs_tied_to_transactions(sentry_init, capture_envelopes):
    """
    Log messages are also tied to transactions.
    """
    sentry_init(enable_logs=True, traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(name="test-transaction") as trx:
        sentry_sdk.logger.warning("This is a log tied to a transaction")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0]["attributes"]["sentry.trace.parent_span_id"] == trx.span_id


@minimum_python_37
def test_logs_tied_to_spans(sentry_init, capture_envelopes):
    """
    Log messages are also tied to spans.
    """
    sentry_init(enable_logs=True, traces_sample_rate=1.0)
    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(name="test-transaction"):
        with sentry_sdk.start_span(name="test-span") as span:
            sentry_sdk.logger.warning("This is a log tied to a span")

    get_client().flush()
    logs = envelopes_to_logs(envelopes)
    assert logs[0]["attributes"]["sentry.trace.parent_span_id"] == span.span_id


def test_auto_flush_logs_after_100(sentry_init, capture_envelopes):
    """
    If you log >100 logs, it should automatically trigger a flush.
    """
    sentry_init(enable_logs=True)
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    for i in range(200):
        python_logger.warning("log #%d", i)

    for _ in range(500):
        time.sleep(1.0 / 100.0)
        if len(envelopes) > 0:
            return

    raise AssertionError("200 logs were never flushed after five seconds")


def test_log_user_attributes(sentry_init, capture_envelopes):
    """User attributes are sent if enable_logs is True."""
    sentry_init(enable_logs=True)

    sentry_sdk.set_user({"id": "1", "email": "test@example.com", "username": "test"})
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning("Hello, world!")

    get_client().flush()

    logs = envelopes_to_logs(envelopes)
    (log,) = logs

    # Check that all expected user attributes are present.
    assert log["attributes"].items() >= {
        ("user.id", "1"),
        ("user.email", "test@example.com"),
        ("user.name", "test"),
    }


@minimum_python_37
def test_auto_flush_logs_after_5s(sentry_init, capture_envelopes):
    """
    If you log a single log, it should automatically flush after 5 seconds, at most 10 seconds.
    """
    sentry_init(enable_logs=True)
    envelopes = capture_envelopes()

    python_logger = logging.Logger("test-logger")
    python_logger.warning("log #%d", 1)

    for _ in range(100):
        time.sleep(1.0 / 10.0)
        if len(envelopes) > 0:
            return

    raise AssertionError("1 logs was never flushed after 10 seconds")


@minimum_python_37
@pytest.mark.parametrize(
    "message,expected_body,params",
    [
        ("any text with {braces} in it", "any text with {braces} in it", None),
        (
            'JSON data: {"key": "value", "number": 42}',
            'JSON data: {"key": "value", "number": 42}',
            None,
        ),
        ("Multiple {braces} {in} {message}", "Multiple {braces} {in} {message}", None),
        ("Nested {{braces}}", "Nested {{braces}}", None),
        ("Empty braces: {}", "Empty braces: {}", None),
        ("Braces with params: {user}", "Braces with params: alice", {"user": "alice"}),
        (
            "Braces with partial params: {user1} {user2}",
            "Braces with partial params: alice {user2}",
            {"user1": "alice"},
        ),
    ],
)
def test_logs_with_literal_braces(
    sentry_init, capture_envelopes, message, expected_body, params
):
    """
    Test that log messages with literal braces (like JSON) work without crashing.
    This is a regression test for issue #4975.
    """
    sentry_init(enable_logs=True)
    envelopes = capture_envelopes()

    if params:
        sentry_sdk.logger.info(message, **params)
    else:
        sentry_sdk.logger.info(message)

    get_client().flush()
    logs = envelopes_to_logs(envelopes)

    assert len(logs) == 1
    assert logs[0]["body"] == expected_body

    # Verify template is only stored when there are parameters
    if params:
        assert logs[0]["attributes"]["sentry.message.template"] == message
    else:
        assert "sentry.message.template" not in logs[0]["attributes"]


@minimum_python_37
def test_batcher_drops_logs(sentry_init, monkeypatch):
    sentry_init(enable_logs=True)
    client = sentry_sdk.get_client()

    def no_op_flush():
        pass

    monkeypatch.setattr(client.log_batcher, "_flush", no_op_flush)

    lost_event_calls = []

    def record_lost_event(reason, data_category=None, item=None, *, quantity=1):
        lost_event_calls.append((reason, data_category, item, quantity))

    monkeypatch.setattr(client.log_batcher, "_record_lost_func", record_lost_event)

    for i in range(1_005):  # 5 logs over the hard limit
        sentry_sdk.logger.info("This is a 'info' log...")

    assert len(lost_event_calls) == 5
    for lost_event_call in lost_event_calls:
        assert lost_event_call == ("queue_overflow", "log_item", None, 1)
