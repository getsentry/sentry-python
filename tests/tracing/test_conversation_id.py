from sentry_conventions.attributes import ATTRIBUTE_NAMES

import sentry_sdk


def test_conversation_id_propagates_to_span_with_gen_ai_operation_name(
    sentry_init, capture_items
):
    """Span with gen_ai.operation.name attribute should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-op-name-test")

    with sentry_sdk.start_span(name="test-sg"):
        with sentry_sdk.start_span(
            name="client", attributes={ATTRIBUTE_NAMES.SENTRY_OP: "http.client"}
        ) as span:
            span.set_attribute(ATTRIBUTE_NAMES.GEN_AI_OPERATION_NAME, "chat")

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert (
        span["attributes"].get(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID)
        == "conv-op-name-test"
    )
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_propagates_to_span_with_ai_op(sentry_init, capture_items):
    """Span with ai.* op should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-ai-op-test")

    with sentry_sdk.start_span(name="test-tx"):
        with sentry_sdk.start_span(
            name="completion",
            attributes={ATTRIBUTE_NAMES.SENTRY_OP: "ai.chat.completions"},
        ):
            pass

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert (
        span["attributes"].get(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID)
        == "conv-ai-op-test"
    )
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_propagates_to_span_with_gen_ai_op(
    sentry_init,
    capture_items,
):
    """Span with gen_ai.* op should get conversation_id."""
    sentry_init(
        traces_sample_rate=1.0,
    )

    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-gen-ai-op-test")

    with sentry_sdk.start_span(name="test-tx"):
        with sentry_sdk.start_span(
            name="invoke", attributes={ATTRIBUTE_NAMES.SENTRY_OP: "gen_ai.invoke_agent"}
        ):
            pass

    sentry_sdk.flush()

    span, segment = [item.payload for item in items]

    assert (
        span["attributes"].get(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID)
        == "conv-gen-ai-op-test"
    )
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_not_propagated_to_non_ai_span(sentry_init, capture_items):
    """Non-AI span should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-should-not-appear")

    with sentry_sdk.start_span(name="test-sg"):
        with sentry_sdk.start_span(
            name="client", attributes={ATTRIBUTE_NAMES.SENTRY_OP: "http.client"}
        ) as span:
            span.set_attribute("some.other.data", "value")

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in span["attributes"]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_not_propagated_when_not_set(sentry_init, capture_items):
    """AI span should not have conversation_id if not set on scope."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.remove_conversation_id()

    with sentry_sdk.start_span(name="test-sg"):
        with sentry_sdk.start_span(
            name="completion",
            attributes={ATTRIBUTE_NAMES.SENTRY_OP: "ai.chat.completions"},
        ):
            pass

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in span["attributes"]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_not_propagated_to_span_without_op(sentry_init, capture_items):
    """Span without op and without gen_ai.operation.name should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-no-op-test")

    with sentry_sdk.start_span(name="test-sg"):
        with sentry_sdk.start_span(name="unnamed-span") as span:
            span.set_attribute("regular.data", "value")

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in span["attributes"]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_propagates_with_gen_ai_operation_name_no_op(
    sentry_init, capture_items
):
    """Span with gen_ai.operation.name but no op should still get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-no-op-but-data-test")

    with sentry_sdk.start_span(name="test-sg"):
        with sentry_sdk.start_span(name="unnamed-span") as span:
            span.set_attribute(ATTRIBUTE_NAMES.GEN_AI_OPERATION_NAME, "embedding")

    sentry_sdk.flush()

    (
        span,
        segment,
    ) = [item.payload for item in items]
    assert (
        span["attributes"].get(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID)
        == "conv-no-op-but-data-test"
    )
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]


def test_conversation_id_propagates_to_segment_with_ai_op(sentry_init, capture_items):
    """Segment with ai.* op should get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-tx-ai-op-test")

    with sentry_sdk.start_span(
        name="AI Workflow", attributes={ATTRIBUTE_NAMES.SENTRY_OP: "ai.workflow"}
    ):
        pass

    sentry_sdk.flush()

    (segment,) = [item.payload for item in items]
    assert (
        segment["attributes"].get(ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID)
        == "conv-tx-ai-op-test"
    )


def test_conversation_id_not_propagated_to_non_ai_segment(sentry_init, capture_items):
    """Non-AI segment should NOT get conversation_id."""
    sentry_init(traces_sample_rate=1.0)
    items = capture_items("span")

    scope = sentry_sdk.get_current_scope()
    scope.set_conversation_id("conv-tx-should-not-appear")

    with sentry_sdk.start_span(
        name="HTTP Request", attributes={ATTRIBUTE_NAMES.SENTRY_OP: "http.server"}
    ):
        pass

    sentry_sdk.flush()

    (segment,) = [item.payload for item in items]
    assert ATTRIBUTE_NAMES.GEN_AI_CONVERSATION_ID not in segment["attributes"]
