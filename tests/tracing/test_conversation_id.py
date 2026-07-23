import pytest

import sentry_sdk
from sentry_sdk import start_span


def test_conversation_id_propagates_to_span_with_gen_ai_operation_name(
    sentry_init, capture_events
):
    """Span with gen_ai.operation.name data should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-op-name-test")

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(op="http.client") as span:
            span.set_data("gen_ai.operation.name", "chat")

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert span_data.get("gen_ai.conversation.id") == "conv-op-name-test"


def test_conversation_id_propagates_to_span_with_ai_op(sentry_init, capture_events):
    """Span with ai.* op should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-ai-op-test")

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(op="ai.chat.completions"):
            pass

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert span_data.get("gen_ai.conversation.id") == "conv-ai-op-test"


@pytest.mark.parametrize("stream_gen_ai_spans", [True, False])
def test_conversation_id_propagates_to_span_with_gen_ai_op(
    sentry_init, capture_events, capture_items, stream_gen_ai_spans
):
    """Span with gen_ai.* op should get conversation_id."""
    sentry_init(
        traces_sample_rate=1.0,
        stream_gen_ai_spans=stream_gen_ai_spans,
    )

    if stream_gen_ai_spans:
        items = capture_items("span")

        scope = sentry_sdk.get_current_scope()
        scope.set_conversation_id("conv-gen-ai-op-test")

        with sentry_sdk.start_transaction(name="test-tx"):
            with start_span(op="gen_ai.invoke_agent"):
                pass

        spans = [item.payload for item in items]
        span_data = spans[0]["attributes"]
    else:
        events = capture_events()

        scope = sentry_sdk.get_current_scope()
        scope.set_conversation_id("conv-gen-ai-op-test")

        with sentry_sdk.start_transaction(name="test-tx"):
            with start_span(op="gen_ai.invoke_agent"):
                pass

        (event,) = events
        span_data = event["spans"][0]["data"]

    assert span_data.get("gen_ai.conversation.id") == "conv-gen-ai-op-test"


def test_conversation_id_not_propagated_to_non_ai_span(sentry_init, capture_events):
    """Non-AI span should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-should-not-appear")

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(op="http.client") as span:
            span.set_data("some.other.data", "value")

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert "gen_ai.conversation.id" not in span_data


def test_conversation_id_not_propagated_when_not_set(sentry_init, capture_events):
    """AI span should not have conversation_id if not set on scope."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    # Ensure no conversation_id is set
    scope = sentry_sdk.get_current_scope()
    scope.remove_conversation_id()

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(op="ai.chat.completions"):
            pass

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert "gen_ai.conversation.id" not in span_data


def test_conversation_id_not_propagated_to_span_without_op(sentry_init, capture_events):
    """Span without op and without gen_ai.operation.name should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-no-op-test")

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(name="unnamed-span") as span:
            span.set_data("regular.data", "value")

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert "gen_ai.conversation.id" not in span_data


def test_conversation_id_propagates_with_gen_ai_operation_name_no_op(
    sentry_init, capture_events
):
    """Span with gen_ai.operation.name but no op should still get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-no-op-but-data-test")

    with sentry_sdk.start_transaction(name="test-tx"):
        with start_span(name="unnamed-span") as span:
            span.set_data("gen_ai.operation.name", "embedding")

    (event,) = events
    span_data = event["spans"][0]["data"]
    assert span_data.get("gen_ai.conversation.id") == "conv-no-op-but-data-test"


def test_conversation_id_propagates_to_transaction_with_ai_op(
    sentry_init, capture_events
):
    """Transaction with ai.* op should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-tx-ai-op-test")

    with sentry_sdk.start_transaction(op="ai.workflow", name="AI Workflow"):
        pass

    (event,) = events
    trace_data = event["contexts"]["trace"]["data"]
    assert trace_data.get("gen_ai.conversation.id") == "conv-tx-ai-op-test"


def test_conversation_id_not_propagated_to_non_ai_transaction(
    sentry_init, capture_events
):
    """Non-AI transaction should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-tx-should-not-appear")

    with sentry_sdk.start_transaction(op="http.server", name="HTTP Request"):
        pass

    (event,) = events
    trace_data = event["contexts"]["trace"]["data"]
    assert "gen_ai.conversation.id" not in trace_data
