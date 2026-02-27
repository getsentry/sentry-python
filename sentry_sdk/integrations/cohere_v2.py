import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.ai.utils import (
    set_data_normalized,
    normalize_message_roles,
    truncate_and_annotate_messages,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, reraise

from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    COLLECTED_CHAT_PARAMS,
    _capture_exception,
)

try:
    from cohere.v2.client import V2Client as CohereV2Client

    # Type locations changed between cohere versions:
    # 5.13.x: cohere.types (ChatResponse, MessageEndStreamedChatResponseV2)
    # 5.20+:  cohere.v2.types (V2ChatResponse, MessageEndV2ChatStreamResponse)
    try:
        from cohere.v2.types import V2ChatResponse
        from cohere.v2.types import MessageEndV2ChatStreamResponse

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
    """Called from CohereIntegration.setup_once() to patch V2Client methods.

    The embed wrapper is passed in from cohere.py to reuse the same _wrap_embed
    for both V1 and V2, since the embed response format (.meta.billed_units)
    is identical across both API versions.
    """
    if not _has_v2:
        return

    CohereV2Client.chat = _wrap_chat_v2(CohereV2Client.chat, streaming=False)
    CohereV2Client.chat_stream = _wrap_chat_v2(
        CohereV2Client.chat_stream, streaming=True
    )
    CohereV2Client.embed = wrap_embed_fn(CohereV2Client.embed)


def _extract_messages_v2(messages):
    # type: (Any) -> list[dict[str, str]]
    """Extract role/content dicts from V2-style message objects.

    Handles both plain dicts and Pydantic model instances.
    """
    result = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
        else:
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                (
                    item.get("text", "")
                    if isinstance(item, dict)
                    else getattr(item, "text", "")
                )
                for item in content
                if (isinstance(item, dict) and "text" in item) or hasattr(item, "text")
            )
        else:
            text = str(content) if content else ""
        result.append({"role": role, "content": text})
    return result


def _record_token_usage_v2(span, usage):
    # type: (Span, Any) -> None
    """Extract and record token usage from a V2 Usage object."""
    if hasattr(usage, "billed_units") and usage.billed_units is not None:
        record_token_usage(
            span,
            input_tokens=getattr(usage.billed_units, "input_tokens", None),
            output_tokens=getattr(usage.billed_units, "output_tokens", None),
        )
    elif hasattr(usage, "tokens") and usage.tokens is not None:
        record_token_usage(
            span,
            input_tokens=getattr(usage.tokens, "input_tokens", None),
            output_tokens=getattr(usage.tokens, "output_tokens", None),
        )


def _wrap_chat_v2(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    def collect_v2_response_fields(span, res, include_pii):
        # type: (Span, V2ChatResponse, bool) -> None
        if include_pii:
            if (
                hasattr(res, "message")
                and hasattr(res.message, "content")
                and res.message.content
            ):
                texts = [
                    item.text for item in res.message.content if hasattr(item, "text")
                ]
                if texts:
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, texts)

            if (
                hasattr(res, "message")
                and hasattr(res.message, "tool_calls")
                and res.message.tool_calls
            ):
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                    res.message.tool_calls,
                )

        if hasattr(res, "id"):
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_ID, res.id)

        if hasattr(res, "finish_reason"):
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, res.finish_reason
            )

        if hasattr(res, "usage") and res.usage is not None:
            _record_token_usage_v2(span, res.usage)

    @wraps(f)
    def new_chat(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)

        if integration is None or "messages" not in kwargs:
            return f(*args, **kwargs)

        model = kwargs.get("model", "")

        span = sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model}".strip(),
            origin=CohereIntegration.origin,
        )
        span.__enter__()
        try:
            res = f(*args, **kwargs)
        except Exception as e:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(e)
                span.__exit__(None, None, None)
            reraise(*exc_info)

        with capture_internal_exceptions():
            set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "cohere")
            set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "chat")
            if model:
                set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, model)

            if should_send_default_pii() and integration.include_prompts:
                messages = _extract_messages_v2(kwargs.get("messages", []))
                messages = normalize_message_roles(messages)
                scope = sentry_sdk.get_current_scope()
                messages_data = truncate_and_annotate_messages(messages, span, scope)
                if messages_data is not None:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        messages_data,
                        unpack=False,
                    )
                if "tools" in kwargs:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
                        kwargs["tools"],
                    )

            for k, v in COLLECTED_CHAT_PARAMS.items():
                if k in kwargs:
                    set_data_normalized(span, v, kwargs[k])
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_STREAMING, streaming)

            if streaming:
                old_iterator = res

                def new_iterator():
                    # type: () -> Iterator[V2ChatStreamResponse]
                    collected_text = []
                    with capture_internal_exceptions():
                        for x in old_iterator:
                            if (
                                hasattr(x, "type")
                                and x.type == "content-delta"
                                and hasattr(x, "delta")
                                and x.delta is not None
                            ):
                                msg = getattr(x.delta, "message", None)
                                if msg is not None:
                                    content = getattr(msg, "content", None)
                                    if content is not None and hasattr(content, "text"):
                                        collected_text.append(content.text)

                            if isinstance(x, MessageEndV2ChatStreamResponse):
                                include_pii = (
                                    should_send_default_pii()
                                    and integration.include_prompts
                                )
                                if include_pii and collected_text:
                                    set_data_normalized(
                                        span,
                                        SPANDATA.GEN_AI_RESPONSE_TEXT,
                                        ["".join(collected_text)],
                                    )
                                if hasattr(x, "id"):
                                    set_data_normalized(
                                        span, SPANDATA.GEN_AI_RESPONSE_ID, x.id
                                    )
                                if hasattr(x, "delta") and x.delta is not None:
                                    if hasattr(x.delta, "finish_reason"):
                                        set_data_normalized(
                                            span,
                                            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
                                            x.delta.finish_reason,
                                        )
                                    if (
                                        hasattr(x.delta, "usage")
                                        and x.delta.usage is not None
                                    ):
                                        _record_token_usage_v2(span, x.delta.usage)
                            yield x

                    span.__exit__(None, None, None)

                return new_iterator()
            elif isinstance(res, V2ChatResponse):
                collect_v2_response_fields(
                    span,
                    res,
                    include_pii=should_send_default_pii()
                    and integration.include_prompts,
                )
                span.__exit__(None, None, None)
            else:
                set_data_normalized(span, "unknown_response", True)
                span.__exit__(None, None, None)
            return res

    return new_chat
