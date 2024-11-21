from unittest import mock
import pytest

from string import Template
from typing import Dict

import sentry_sdk
from sentry_sdk.integrations.rust_tracing import (
    RustTracingIntegration,
    RustTracingLayer,
    RustTracingLevel,
    EventTypeMapping,
)
from sentry_sdk import start_transaction, capture_message


def _test_event_type_mapping(metadata: Dict[str, object]) -> EventTypeMapping:
    level = RustTracingLevel(metadata.get("level"))
    if level == RustTracingLevel.Error:
        return EventTypeMapping.Exc
    elif level in (RustTracingLevel.Warn, RustTracingLevel.Info):
        return EventTypeMapping.Breadcrumb
    elif level == RustTracingLevel.Debug:
        return EventTypeMapping.Event
    elif level == RustTracingLevel.Trace:
        return EventTypeMapping.Ignore
    else:
        return EventTypeMapping.Ignore


class FakeRustTracing:
    # Parameters: `level`, `index`
    span_template = Template(
        """{"index":$index,"is_root":false,"metadata":{"fields":["index","use_memoized","version"],"file":"src/lib.rs","is_event":false,"is_span":true,"level":"$level","line":40,"module_path":"_bindings","name":"fibonacci","target":"_bindings"},"parent":null,"use_memoized":true}"""
    )

    # Parameters: `level`, `index`
    event_template = Template(
        """{"message":"Getting the ${index}th fibonacci number","metadata":{"fields":["message"],"file":"src/lib.rs","is_event":true,"is_span":false,"level":"$level","line":23,"module_path":"_bindings","name":"event src/lib.rs:23","target":"_bindings"}}"""
    )

    def __init__(self):
        self.spans = {}

    def set_layer_impl(self, layer: RustTracingLayer):
        self.layer = layer

    def new_span(self, level: RustTracingLevel, span_id: int, index_arg: int = 10):
        span_attrs = self.span_template.substitute(level=level.value, index=index_arg)
        state = self.layer.on_new_span(span_attrs, str(span_id))
        self.spans[span_id] = state

    def close_span(self, span_id: int):
        state = self.spans.pop(span_id)
        self.layer.on_close(str(span_id), state)

    def event(self, level: RustTracingLevel, span_id: int, index_arg: int = 10):
        event = self.event_template.substitute(level=level.value, index=index_arg)
        state = self.spans[span_id]
        self.layer.on_event(event, state)

    def record(self, span_id: int):
        state = self.spans[span_id]
        self.layer.on_record(str(span_id), """{"version": "memoized"}""", state)


def test_on_new_span_on_close(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_new_span_on_close",
        initializer=rust_tracing.set_layer_impl,
        include_tracing_fields=True,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        sentry_first_rust_span = sentry_sdk.get_current_span()
        _, rust_first_rust_span = rust_tracing.spans[3]

        assert sentry_first_rust_span == rust_first_rust_span

        rust_tracing.close_span(3)
        assert sentry_sdk.get_current_span() != sentry_first_rust_span

    (event,) = events
    assert len(event["spans"]) == 1

    # Ensure the span metadata is wired up
    span = event["spans"][0]
    assert span["op"] == "function"
    assert span["origin"] == "auto.function.rust_tracing.test_on_new_span_on_close"
    assert span["description"] == "_bindings::fibonacci"

    # Ensure the span was opened/closed appropriately
    assert span["start_timestamp"] is not None
    assert span["timestamp"] is not None

    # Ensure the extra data from Rust is hooked up
    data = span["data"]
    assert data["use_memoized"]
    assert data["index"] == 10
    assert data["version"] is None


def test_nested_on_new_span_on_close(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_nested_on_new_span_on_close",
        initializer=rust_tracing.set_layer_impl,
        include_tracing_fields=True,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    with start_transaction():
        original_sentry_span = sentry_sdk.get_current_span()

        rust_tracing.new_span(RustTracingLevel.Info, 3, index_arg=10)
        sentry_first_rust_span = sentry_sdk.get_current_span()
        _, rust_first_rust_span = rust_tracing.spans[3]

        # Use a different `index_arg` value for the inner span to help
        # distinguish the two at the end of the test
        rust_tracing.new_span(RustTracingLevel.Info, 5, index_arg=9)
        sentry_second_rust_span = sentry_sdk.get_current_span()
        rust_parent_span, rust_second_rust_span = rust_tracing.spans[5]

        assert rust_second_rust_span == sentry_second_rust_span
        assert rust_parent_span == sentry_first_rust_span
        assert rust_parent_span == rust_first_rust_span
        assert rust_parent_span != rust_second_rust_span

        rust_tracing.close_span(5)

        # Ensure the current sentry span was moved back to the parent
        sentry_span_after_close = sentry_sdk.get_current_span()
        assert sentry_span_after_close == sentry_first_rust_span

        rust_tracing.close_span(3)

        assert sentry_sdk.get_current_span() == original_sentry_span

    (event,) = events
    assert len(event["spans"]) == 2

    # Ensure the span metadata is wired up for all spans
    first_span, second_span = event["spans"]
    assert first_span["op"] == "function"
    assert (
        first_span["origin"]
        == "auto.function.rust_tracing.test_nested_on_new_span_on_close"
    )
    assert first_span["description"] == "_bindings::fibonacci"
    assert second_span["op"] == "function"
    assert (
        second_span["origin"]
        == "auto.function.rust_tracing.test_nested_on_new_span_on_close"
    )
    assert second_span["description"] == "_bindings::fibonacci"

    # Ensure the spans were opened/closed appropriately
    assert first_span["start_timestamp"] is not None
    assert first_span["timestamp"] is not None
    assert second_span["start_timestamp"] is not None
    assert second_span["timestamp"] is not None

    # Ensure the extra data from Rust is hooked up in both spans
    first_span_data = first_span["data"]
    assert first_span_data["use_memoized"]
    assert first_span_data["index"] == 10
    assert first_span_data["version"] is None

    second_span_data = second_span["data"]
    assert second_span_data["use_memoized"]
    assert second_span_data["index"] == 9
    assert second_span_data["version"] is None


def test_on_new_span_without_transaction(sentry_init):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_new_span_without_transaction", rust_tracing.set_layer_impl
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    assert sentry_sdk.get_current_span() is None

    # Should still create a span hierarchy, it just will not be under a txn
    rust_tracing.new_span(RustTracingLevel.Info, 3)
    current_span = sentry_sdk.get_current_span()
    assert current_span is not None
    assert current_span.containing_transaction is None


def test_on_event_exception(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_event_exception",
        rust_tracing.set_layer_impl,
        event_type_mapping=_test_event_type_mapping,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        # Mapped to Exception
        rust_tracing.event(RustTracingLevel.Error, 3)

        rust_tracing.close_span(3)

    assert len(events) == 2
    exc, _tx = events
    assert exc["level"] == "error"
    assert exc["logger"] == "_bindings"
    assert exc["message"] == "Getting the 10th fibonacci number"
    assert exc["breadcrumbs"]["values"] == []

    location_context = exc["contexts"]["rust_tracing_location"]
    assert location_context["module_path"] == "_bindings"
    assert location_context["file"] == "src/lib.rs"
    assert location_context["line"] == 23

    field_context = exc["contexts"]["rust_tracing_fields"]
    assert field_context["message"] == "Getting the 10th fibonacci number"


def test_on_event_breadcrumb(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_event_breadcrumb",
        rust_tracing.set_layer_impl,
        event_type_mapping=_test_event_type_mapping,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        # Mapped to Breadcrumb
        rust_tracing.event(RustTracingLevel.Info, 3)

        rust_tracing.close_span(3)
        capture_message("test message")

    assert len(events) == 2
    message, _tx = events

    breadcrumbs = message["breadcrumbs"]["values"]
    assert len(breadcrumbs) == 1
    assert breadcrumbs[0]["level"] == "info"
    assert breadcrumbs[0]["message"] == "Getting the 10th fibonacci number"
    assert breadcrumbs[0]["type"] == "default"


def test_on_event_event(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_event_event",
        rust_tracing.set_layer_impl,
        event_type_mapping=_test_event_type_mapping,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        # Mapped to Event
        rust_tracing.event(RustTracingLevel.Debug, 3)

        rust_tracing.close_span(3)

    assert len(events) == 2
    event, _tx = events

    assert event["logger"] == "_bindings"
    assert event["level"] == "debug"
    assert event["message"] == "Getting the 10th fibonacci number"
    assert event["breadcrumbs"]["values"] == []

    location_context = event["contexts"]["rust_tracing_location"]
    assert location_context["module_path"] == "_bindings"
    assert location_context["file"] == "src/lib.rs"
    assert location_context["line"] == 23

    field_context = event["contexts"]["rust_tracing_fields"]
    assert field_context["message"] == "Getting the 10th fibonacci number"


def test_on_event_ignored(sentry_init, capture_events):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_on_event_ignored",
        rust_tracing.set_layer_impl,
        event_type_mapping=_test_event_type_mapping,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    sentry_sdk.get_isolation_scope().clear_breadcrumbs()

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        # Ignored
        rust_tracing.event(RustTracingLevel.Trace, 3)

        rust_tracing.close_span(3)

    assert len(events) == 1
    (tx,) = events
    assert tx["type"] == "transaction"
    assert "message" not in tx


def test_span_filter(sentry_init, capture_events):
    def span_filter(metadata: Dict[str, object]) -> bool:
        return RustTracingLevel(metadata.get("level")) in (
            RustTracingLevel.Error,
            RustTracingLevel.Warn,
            RustTracingLevel.Info,
            RustTracingLevel.Debug,
        )

    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_span_filter",
        initializer=rust_tracing.set_layer_impl,
        span_filter=span_filter,
        include_tracing_fields=True,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    events = capture_events()
    with start_transaction():
        original_sentry_span = sentry_sdk.get_current_span()

        # Span is not ignored
        rust_tracing.new_span(RustTracingLevel.Info, 3, index_arg=10)
        info_span = sentry_sdk.get_current_span()

        # Span is ignored, current span should remain the same
        rust_tracing.new_span(RustTracingLevel.Trace, 5, index_arg=9)
        assert sentry_sdk.get_current_span() == info_span

        # Closing the filtered span should leave the current span alone
        rust_tracing.close_span(5)
        assert sentry_sdk.get_current_span() == info_span

        rust_tracing.close_span(3)
        assert sentry_sdk.get_current_span() == original_sentry_span

    (event,) = events
    assert len(event["spans"]) == 1
    # The ignored span has index == 9
    assert event["spans"][0]["data"]["index"] == 10


def test_record(sentry_init):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_record",
        initializer=rust_tracing.set_layer_impl,
        include_tracing_fields=True,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        span_before_record = sentry_sdk.get_current_span().to_json()
        assert span_before_record["data"]["version"] is None

        rust_tracing.record(3)

        span_after_record = sentry_sdk.get_current_span().to_json()
        assert span_after_record["data"]["version"] == "memoized"


def test_record_in_ignored_span(sentry_init):
    def span_filter(metadata: Dict[str, object]) -> bool:
        # Just ignore Trace
        return RustTracingLevel(metadata.get("level")) != RustTracingLevel.Trace

    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_record_in_ignored_span",
        rust_tracing.set_layer_impl,
        span_filter=span_filter,
        include_tracing_fields=True,
    )
    sentry_init(integrations=[integration], traces_sample_rate=1.0)

    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        span_before_record = sentry_sdk.get_current_span().to_json()
        assert span_before_record["data"]["version"] is None

        rust_tracing.new_span(RustTracingLevel.Trace, 5)
        rust_tracing.record(5)

        # `on_record()` should not do anything to the current Sentry span if the associated Rust span was ignored
        span_after_record = sentry_sdk.get_current_span().to_json()
        assert span_after_record["data"]["version"] is None


@pytest.mark.parametrize(
    "send_default_pii, include_tracing_fields, tracing_fields_expected",
    [
        (True, True, True),
        (True, False, False),
        (True, None, True),
        (False, True, True),
        (False, False, False),
        (False, None, False),
    ],
)
def test_include_tracing_fields(
    sentry_init, send_default_pii, include_tracing_fields, tracing_fields_expected
):
    rust_tracing = FakeRustTracing()
    integration = RustTracingIntegration(
        "test_record",
        initializer=rust_tracing.set_layer_impl,
        include_tracing_fields=include_tracing_fields,
    )

    sentry_init(
        integrations=[integration],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
    )
    with start_transaction():
        rust_tracing.new_span(RustTracingLevel.Info, 3)

        span_before_record = sentry_sdk.get_current_span().to_json()
        if tracing_fields_expected:
            assert span_before_record["data"]["version"] is None
        else:
            assert span_before_record["data"]["version"] == "[Filtered]"

        rust_tracing.record(3)

        span_after_record = sentry_sdk.get_current_span().to_json()

        if tracing_fields_expected:
            assert span_after_record["data"] == {
                "thread.id": mock.ANY,
                "thread.name": mock.ANY,
                "use_memoized": True,
                "version": "memoized",
                "index": 10,
            }

        else:
            assert span_after_record["data"] == {
                "thread.id": mock.ANY,
                "thread.name": mock.ANY,
                "use_memoized": "[Filtered]",
                "version": "[Filtered]",
                "index": "[Filtered]",
            }
