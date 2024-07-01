import pytest

from unittest import mock

import sentry_sdk
from sentry_sdk.hub import Hub
from sentry_sdk.scrubber import EventScrubber, DEFAULT_DENYLIST


"""
Those tests are meant to check the compatibility of the new scopes in SDK 2.0 with the old Hub/Scope system in SDK 1.x.

Those tests have been run with the latest SDK 1.x version and the data used in the `assert` statements represents
the behvaior of the SDK 1.x.

This makes sure that we are backwards compatible. (on a best effort basis, there will probably be some edge cases that are not covered here)
"""


@pytest.fixture
def expected_error():
    def create_expected_error_event(trx, span):
        return {
            "level": "warning-X",
            "exception": {
                "values": [
                    {
                        "mechanism": {"type": "generic", "handled": True},
                        "module": None,
                        "type": "ValueError",
                        "value": "This is a test exception",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "tests/test_new_scopes_compat_event.py",
                                    "abs_path": mock.ANY,
                                    "function": "_faulty_function",
                                    "module": "tests.test_new_scopes_compat_event",
                                    "lineno": mock.ANY,
                                    "pre_context": [
                                        "    return create_expected_transaction_event",
                                        "",
                                        "",
                                        "def _faulty_function():",
                                        "    try:",
                                    ],
                                    "context_line": '        raise ValueError("This is a test exception")',
                                    "post_context": [
                                        "    except ValueError as ex:",
                                        "        sentry_sdk.capture_exception(ex)",
                                        "",
                                        "",
                                        "def _test_before_send(event, hint):",
                                    ],
                                    "vars": {
                                        "ex": mock.ANY,
                                    },
                                    "in_app": True,
                                }
                            ]
                        },
                    }
                ]
            },
            "event_id": mock.ANY,
            "timestamp": mock.ANY,
            "contexts": {
                "character": {
                    "name": "Mighty Fighter changed by before_send",
                    "age": 19,
                    "attack_type": "melee",
                },
                "trace": {
                    "trace_id": trx.trace_id,
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "op": "test_span",
                    "origin": "manual",
                    "description": None,
                    "data": {
                        "thread.id": mock.ANY,
                        "thread.name": "MainThread",
                    },
                },
                "runtime": {
                    "name": "CPython",
                    "version": mock.ANY,
                    "build": mock.ANY,
                },
            },
            "user": {
                "id": "123",
                "email": "jane.doe@example.com",
                "ip_address": "[Filtered]",
            },
            "transaction": "test_transaction",
            "transaction_info": {"source": "custom"},
            "tags": {"tag1": "tag1_value", "tag2": "tag2_value"},
            "extra": {
                "extra1": "extra1_value",
                "extra2": "extra2_value",
                "should_be_removed_by_event_scrubber": "[Filtered]",
                "sys.argv": "[Filtered]",
            },
            "breadcrumbs": {
                "values": [
                    {
                        "category": "error-level",
                        "message": "Authenticated user %s",
                        "level": "error",
                        "data": {"breadcrumb2": "somedata"},
                        "timestamp": mock.ANY,
                        "type": "default",
                    }
                ]
            },
            "modules": mock.ANY,
            "release": "0.1.2rc3",
            "environment": "checking-compatibility-with-sdk1",
            "server_name": mock.ANY,
            "sdk": {
                "name": "sentry.python",
                "version": mock.ANY,
                "packages": [{"name": "pypi:sentry-sdk", "version": mock.ANY}],
                "integrations": [
                    "argv",
                    "atexit",
                    "dedupe",
                    "excepthook",
                    "logging",
                    "modules",
                    "stdlib",
                    "threading",
                ],
            },
            "platform": "python",
            "_meta": {
                "user": {"ip_address": {"": {"rem": [["!config", "s"]]}}},
                "extra": {
                    "should_be_removed_by_event_scrubber": {
                        "": {"rem": [["!config", "s"]]}
                    },
                    "sys.argv": {"": {"rem": [["!config", "s"]]}},
                },
            },
        }

    return create_expected_error_event


@pytest.fixture
def expected_transaction():
    def create_expected_transaction_event(trx, span):
        return {
            "type": "transaction",
            "transaction": "test_transaction changed by before_send_transaction",
            "transaction_info": {"source": "custom"},
            "contexts": {
                "trace": {
                    "trace_id": trx.trace_id,
                    "span_id": trx.span_id,
                    "parent_span_id": None,
                    "op": "test_transaction_op",
                    "origin": "manual",
                    "description": None,
                    "data": {
                        "thread.id": mock.ANY,
                        "thread.name": "MainThread",
                    },
                },
                "character": {
                    "name": "Mighty Fighter changed by before_send_transaction",
                    "age": 19,
                    "attack_type": "melee",
                },
                "runtime": {
                    "name": "CPython",
                    "version": mock.ANY,
                    "build": mock.ANY,
                },
            },
            "tags": {"tag1": "tag1_value", "tag2": "tag2_value"},
            "timestamp": mock.ANY,
            "start_timestamp": mock.ANY,
            "spans": [
                {
                    "data": {
                        "thread.id": mock.ANY,
                        "thread.name": "MainThread",
                    },
                    "trace_id": trx.trace_id,
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "same_process_as_parent": True,
                    "op": "test_span",
                    "origin": "manual",
                    "description": None,
                    "start_timestamp": mock.ANY,
                    "timestamp": mock.ANY,
                }
            ],
            "measurements": {"memory_used": {"value": 456, "unit": "byte"}},
            "event_id": mock.ANY,
            "level": "warning-X",
            "user": {
                "id": "123",
                "email": "jane.doe@example.com",
                "ip_address": "[Filtered]",
            },
            "extra": {
                "extra1": "extra1_value",
                "extra2": "extra2_value",
                "should_be_removed_by_event_scrubber": "[Filtered]",
                "sys.argv": "[Filtered]",
            },
            "release": "0.1.2rc3",
            "environment": "checking-compatibility-with-sdk1",
            "server_name": mock.ANY,
            "sdk": {
                "name": "sentry.python",
                "version": mock.ANY,
                "packages": [{"name": "pypi:sentry-sdk", "version": mock.ANY}],
                "integrations": [
                    "argv",
                    "atexit",
                    "dedupe",
                    "excepthook",
                    "logging",
                    "modules",
                    "stdlib",
                    "threading",
                ],
            },
            "platform": "python",
            "_meta": {
                "user": {"ip_address": {"": {"rem": [["!config", "s"]]}}},
                "extra": {
                    "should_be_removed_by_event_scrubber": {
                        "": {"rem": [["!config", "s"]]}
                    },
                    "sys.argv": {"": {"rem": [["!config", "s"]]}},
                },
            },
        }

    return create_expected_transaction_event


def _faulty_function():
    try:
        raise ValueError("This is a test exception")
    except ValueError as ex:
        sentry_sdk.capture_exception(ex)


def _test_before_send(event, hint):
    event["contexts"]["character"]["name"] += " changed by before_send"
    return event


def _test_before_send_transaction(event, hint):
    event["transaction"] += " changed by before_send_transaction"
    event["contexts"]["character"]["name"] += " changed by before_send_transaction"
    return event


def _test_before_breadcrumb(breadcrumb, hint):
    if breadcrumb["category"] == "info-level":
        return None
    return breadcrumb


def _generate_event_data(scope=None):
    """
    Generates some data to be used in the events sent by the tests.
    """
    sentry_sdk.set_level("warning-X")

    sentry_sdk.add_breadcrumb(
        category="info-level",
        message="Authenticated user %s",
        level="info",
        data={"breadcrumb1": "somedata"},
    )
    sentry_sdk.add_breadcrumb(
        category="error-level",
        message="Authenticated user %s",
        level="error",
        data={"breadcrumb2": "somedata"},
    )

    sentry_sdk.set_context(
        "character",
        {
            "name": "Mighty Fighter",
            "age": 19,
            "attack_type": "melee",
        },
    )

    sentry_sdk.set_extra("extra1", "extra1_value")
    sentry_sdk.set_extra("extra2", "extra2_value")
    sentry_sdk.set_extra("should_be_removed_by_event_scrubber", "XXX")

    sentry_sdk.set_tag("tag1", "tag1_value")
    sentry_sdk.set_tag("tag2", "tag2_value")

    sentry_sdk.set_user(
        {"id": "123", "email": "jane.doe@example.com", "ip_address": "211.161.1.124"}
    )

    sentry_sdk.set_measurement("memory_used", 456, "byte")

    if scope is not None:
        scope.add_attachment(bytes=b"Hello World", filename="hello.txt")


def _init_sentry_sdk(sentry_init):
    sentry_init(
        environment="checking-compatibility-with-sdk1",
        release="0.1.2rc3",
        before_send=_test_before_send,
        before_send_transaction=_test_before_send_transaction,
        before_breadcrumb=_test_before_breadcrumb,
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST
            + ["should_be_removed_by_event_scrubber", "sys.argv"]
        ),
        send_default_pii=False,
        traces_sample_rate=1.0,
    )


#
# The actual Tests start here!
#


def test_event(sentry_init, capture_envelopes, expected_error, expected_transaction):
    _init_sentry_sdk(sentry_init)

    envelopes = capture_envelopes()

    with sentry_sdk.start_transaction(
        name="test_transaction", op="test_transaction_op"
    ) as trx:
        with sentry_sdk.start_span(op="test_span") as span:
            with sentry_sdk.configure_scope() as scope:  # configure scope
                _generate_event_data(scope)
                _faulty_function()

    (error_envelope, transaction_envelope) = envelopes

    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()
    attachment = error_envelope.items[-1]

    assert error == expected_error(trx, span)
    assert transaction == expected_transaction(trx, span)
    assert attachment.headers == {
        "filename": "hello.txt",
        "type": "attachment",
        "content_type": "text/plain",
    }
    assert attachment.payload.bytes == b"Hello World"


def test_event2(sentry_init, capture_envelopes, expected_error, expected_transaction):
    _init_sentry_sdk(sentry_init)

    envelopes = capture_envelopes()

    with Hub(Hub.current):
        sentry_sdk.set_tag("A", 1)  # will not be added

    with Hub.current:  # with hub
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("B", 1)  # will not be added

        with sentry_sdk.start_transaction(
            name="test_transaction", op="test_transaction_op"
        ) as trx:
            with sentry_sdk.start_span(op="test_span") as span:
                with sentry_sdk.configure_scope() as scope:  # configure scope
                    _generate_event_data(scope)
                    _faulty_function()

    (error_envelope, transaction_envelope) = envelopes

    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()
    attachment = error_envelope.items[-1]

    assert error == expected_error(trx, span)
    assert transaction == expected_transaction(trx, span)
    assert attachment.headers == {
        "filename": "hello.txt",
        "type": "attachment",
        "content_type": "text/plain",
    }
    assert attachment.payload.bytes == b"Hello World"


def test_event3(sentry_init, capture_envelopes, expected_error, expected_transaction):
    _init_sentry_sdk(sentry_init)

    envelopes = capture_envelopes()

    with Hub(Hub.current):
        sentry_sdk.set_tag("A", 1)  # will not be added

    with Hub.current:  # with hub
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("B", 1)  # will not be added

        with sentry_sdk.push_scope() as scope:  # push scope
            with sentry_sdk.start_transaction(
                name="test_transaction", op="test_transaction_op"
            ) as trx:
                with sentry_sdk.start_span(op="test_span") as span:
                    _generate_event_data(scope)
                    _faulty_function()

    (error_envelope, transaction_envelope) = envelopes

    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()
    attachment = error_envelope.items[-1]

    assert error == expected_error(trx, span)
    assert transaction == expected_transaction(trx, span)
    assert attachment.headers == {
        "filename": "hello.txt",
        "type": "attachment",
        "content_type": "text/plain",
    }
    assert attachment.payload.bytes == b"Hello World"


def test_event4(sentry_init, capture_envelopes, expected_error, expected_transaction):
    _init_sentry_sdk(sentry_init)

    envelopes = capture_envelopes()

    with Hub(Hub.current):
        sentry_sdk.set_tag("A", 1)  # will not be added

    with Hub(Hub.current):  # with hub clone
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("B", 1)  # will not be added

        with sentry_sdk.start_transaction(
            name="test_transaction", op="test_transaction_op"
        ) as trx:
            with sentry_sdk.start_span(op="test_span") as span:
                with sentry_sdk.configure_scope() as scope:  # configure scope
                    _generate_event_data(scope)
                    _faulty_function()

    (error_envelope, transaction_envelope) = envelopes

    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()
    attachment = error_envelope.items[-1]

    assert error == expected_error(trx, span)
    assert transaction == expected_transaction(trx, span)
    assert attachment.headers == {
        "filename": "hello.txt",
        "type": "attachment",
        "content_type": "text/plain",
    }
    assert attachment.payload.bytes == b"Hello World"


def test_event5(sentry_init, capture_envelopes, expected_error, expected_transaction):
    _init_sentry_sdk(sentry_init)

    envelopes = capture_envelopes()

    with Hub(Hub.current):
        sentry_sdk.set_tag("A", 1)  # will not be added

    with Hub(Hub.current):  # with hub clone
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("B", 1)  # will not be added

        with sentry_sdk.push_scope() as scope:  # push scope
            with sentry_sdk.start_transaction(
                name="test_transaction", op="test_transaction_op"
            ) as trx:
                with sentry_sdk.start_span(op="test_span") as span:
                    _generate_event_data(scope)
                    _faulty_function()

    (error_envelope, transaction_envelope) = envelopes

    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()
    attachment = error_envelope.items[-1]

    assert error == expected_error(trx, span)
    assert transaction == expected_transaction(trx, span)
    assert attachment.headers == {
        "filename": "hello.txt",
        "type": "attachment",
        "content_type": "text/plain",
    }
    assert attachment.payload.bytes == b"Hello World"
