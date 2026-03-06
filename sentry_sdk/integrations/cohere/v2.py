import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import set_data_normalized, normalize_message_roles
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    _capture_exception,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, reraise

try:
    from cohere.v2.client import V2Client as CohereV2Client

    try:
        from cohere.v2.types import MessageEndV2ChatStreamResponse, V2ChatResponse

        if TYPE_CHECKING:
            from cohere.v2.types import V2ChatStreamResponse
    except ImportError:
        from cohere.types import ChatResponse as V2ChatResponse
        from cohere.types import (
            MessageEndStreamedChatResponseV2 as MessageEndV2ChatStreamResponse,
        )

        if TYPE_CHECKING:
            from cohere.types import StreamedChatResponseV2 as V2ChatStreamResponse

    _has_v2 = True
except ImportError:
    _has_v2 = False


def setup_v2(wrap_embed_fn):
    # type: (Callable[..., Any]) -> None
    if not _has_v2:
        return
    CohereV2Client.chat = _wrap_chat_v2(CohereV2Client.chat, streaming=False)
    CohereV2Client.chat_stream = _wrap_chat_v2(
        CohereV2Client.chat_stream, streaming=True
    )
    CohereV2Client.embed = wrap_embed_fn(CohereV2Client.embed)


def _collect_v2_response_fields(span, response, include_pii):
    # type: (Span, Any, bool) -> None
    model = getattr(response, "model", None)
    if model:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, model)

    response_id = getattr(response, "id", None)
    if response_id:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_ID, response_id)

    finish_reason = getattr(response, "finish_reason", None)
    if finish_reason:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reason)

    if include_pii:
        response_text = _extract_v2_response_text(response)
        if response_text:
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, response_text)

        tool_calls = getattr(getattr(response, "message", None), "tool_calls", None)
        if tool_calls:
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, tool_calls)

    usage = getattr(response, "usage", None)
    if usage:
        billed = getattr(usage, "billed_units", None)
        tokens = getattr(usage, "tokens", None)
        src = billed or tokens
        if src:
            record_token_usage(
                span,
                input_tokens=getattr(src, "input_tokens", None),
                output_tokens=getattr(src, "output_tokens", None),
            )


def _wrap_chat_v2(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    @wraps(f)
    def new_chat(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if integration is None or "messages" not in kwargs:
            return f(*args, **kwargs)

        model = kwargs.get("model", "")
        include_pii = should_send_default_pii() and integration.include_prompts

        span = sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model}".strip(),
            origin=CohereIntegration.origin,
        )
        span.__enter__()
        try:
            response = f(*args, **kwargs)
        except Exception as e:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(e)
                span.__exit__(None, None, None)
            reraise(*exc_info)

        with capture_internal_exceptions():
            set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "cohere")
            set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "chat")
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_STREAMING, streaming)

            if model:
                set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if include_pii:
                messages = _extract_v2_messages(kwargs.get("messages", []))
                messages = normalize_message_roles(messages)
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    messages,
                    unpack=False,
                )

                tools = kwargs.get("tools")
                if tools:
                    set_data_normalized(
                        span, SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, tools
                    )

            if streaming:
                return _iter_v2_stream_events(response, span, include_pii)

            _collect_v2_response_fields(span, response, include_pii)
            span.__exit__(None, None, None)
            return response

    return new_chat


def _iter_v2_stream_events(old_iterator, span, include_pii):
    # type: (Any, Span, bool) -> Iterator[V2ChatStreamResponse]
    collected_text = []  # type: list[str]
    for x in old_iterator:
        with capture_internal_exceptions():
            _append_stream_delta_text(collected_text, x)
            if isinstance(x, MessageEndV2ChatStreamResponse):
                _collect_v2_stream_end_fields(span, x, collected_text, include_pii)
        yield x

    span.__exit__(None, None, None)


def _collect_v2_stream_end_fields(span, event, collected_text, include_pii):
    # type: (Span, Any, list[str], bool) -> None
    delta = getattr(event, "delta", None)
    if not delta:
        return

    response_id = getattr(event, "id", None)
    if response_id:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_ID, response_id)

    finish_reason = getattr(delta, "finish_reason", None)
    if finish_reason:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reason)

    if include_pii and collected_text:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, ["".join(collected_text)]
        )

    usage = getattr(delta, "usage", None)
    if usage:
        billed = getattr(usage, "billed_units", None)
        tokens = getattr(usage, "tokens", None)
        src = billed or tokens
        if src:
            record_token_usage(
                span,
                input_tokens=getattr(src, "input_tokens", None),
                output_tokens=getattr(src, "output_tokens", None),
            )


def _append_stream_delta_text(collected_text, event):
    # type: (list[str], Any) -> None
    if getattr(event, "type", None) != "content-delta":
        return
    delta = getattr(event, "delta", None)
    if not delta:
        return
    message = getattr(delta, "message", None)
    if not message:
        return
    content = getattr(message, "content", None)
    if not content:
        return
    text = getattr(content, "text", None)
    if text is not None:
        collected_text.append(text)


def _extract_v2_messages(messages):
    # type: (Any) -> list[dict[str, Any]]
    result = []
    for msg in messages:
        role = msg["role"] if isinstance(msg, dict) else getattr(msg, "role", "unknown")
        content = (
            msg["content"] if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        result.append({"role": role, "content": content})
    return result


def _extract_v2_response_text(response):
    # type: (Any) -> list[str] | None
    message = getattr(response, "message", None)
    if not message:
        return None
    content = getattr(message, "content", None)
    if not content:
        return None
    texts = [item.text for item in content if hasattr(item, "text")]
    return texts if texts else None
