import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.span_config import set_input_span_data
from sentry_sdk.ai.utils import set_data_normalized, transform_message_content
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
from sentry_sdk.integrations.cohere.utils import (
    get_first_from_sources,
    set_span_data_from_sources,
    transitive_getattr,
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

CHAT_RESPONSE_SOURCES = {
    SPANDATA.GEN_AI_RESPONSE_ID: [("id",)],
    SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
}
PII_CHAT_RESPONSE_SOURCES = {
    SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("message", "tool_calls")],
}
CHAT_USAGE_SOURCES = [("usage",)]
STREAM_DELTA_TEXT_SOURCES = [("delta", "message", "content", "text")]
STREAM_CHAT_RESPONSE_SOURCES = {
    SPANDATA.GEN_AI_RESPONSE_ID: [("id",)],
    SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("delta", "finish_reason")],
}
STREAM_CHAT_USAGE_SOURCES = [("delta", "usage")]
USAGE_UNIT_SOURCES = [("billed_units",), ("tokens",)]
USAGE_TOKEN_SOURCES = {
    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: [("input_tokens",)],
    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: [("output_tokens",)],
}


def setup_v2(wrap_embed_fn):
    # type: (Callable[..., Any]) -> None
    if not _has_v2:
        return
    CohereV2Client.chat = _wrap_chat_v2(CohereV2Client.chat, streaming=False)
    CohereV2Client.chat_stream = _wrap_chat_v2(
        CohereV2Client.chat_stream, streaming=True
    )
    CohereV2Client.embed = wrap_embed_fn(CohereV2Client.embed)


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

        with sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model}".strip(),
            origin=CohereIntegration.origin,
        ) as span:
            try:
                res = f(*args, **kwargs)
            except Exception as e:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    _capture_exception(e)
                reraise(*exc_info)

            with capture_internal_exceptions():
                extra = {SPANDATA.GEN_AI_RESPONSE_STREAMING: streaming}
                if model:
                    set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)
                    extra[SPANDATA.GEN_AI_RESPONSE_MODEL] = model
                set_input_span_data(
                    span,
                    kwargs,
                    integration,
                    {
                        "system": "cohere",
                        "operation": "chat",
                        "pii_params": {
                            "tools": SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
                        },
                        "extract_messages": lambda kw: _extract_messages_v2(
                            kw.get("messages", [])
                        ),
                        "extra_static": extra,
                    },
                )
                if streaming:
                    return _iter_v2_stream_events(res, span, include_pii)
                _collect_v2_response_fields(span, res, include_pii=include_pii)
                return res

    return new_chat


def _extract_messages_v2(messages):
    # type: (Any) -> list[dict[str, Any]]
    result = []
    for msg in messages:
        role = msg["role"] if isinstance(msg, dict) else getattr(msg, "role", "unknown")
        content = (
            msg["content"] if isinstance(msg, dict) else getattr(msg, "content", "")
        )
        result.append({"role": role, "content": transform_message_content(content)})
    return result


def _iter_v2_stream_events(old_iterator, span, include_pii):
    # type: (Any, Span, bool) -> Iterator[V2ChatStreamResponse]
    collected_text = []
    with capture_internal_exceptions():
        for x in old_iterator:
            _append_stream_delta_text(collected_text, x)
            if isinstance(x, MessageEndV2ChatStreamResponse):
                _collect_v2_stream_end_fields(span, x, include_pii, collected_text)
            yield x


def _append_stream_delta_text(collected_text, event):
    # type: (list[str], Any) -> None
    if transitive_getattr(event, "type") != "content-delta":
        return
    content_text = get_first_from_sources(event, STREAM_DELTA_TEXT_SOURCES)
    if content_text is not None:
        collected_text.append(content_text)


def _collect_v2_stream_end_fields(span, event, include_pii, collected_text):
    # type: (Span, Any, bool, list[str]) -> None
    if include_pii and collected_text:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, ["".join(collected_text)]
        )
    set_span_data_from_sources(
        span, event, STREAM_CHAT_RESPONSE_SOURCES, require_truthy=False
    )
    stream_usage = get_first_from_sources(event, STREAM_CHAT_USAGE_SOURCES)
    if stream_usage is not None:
        _record_token_usage_v2(span, stream_usage)


def _collect_v2_response_fields(span, response, include_pii):
    # type: (Span, V2ChatResponse, bool) -> None
    if include_pii:
        content = get_first_from_sources(response, [("message", "content")], True)
        if content:
            texts = [item.text for item in content if hasattr(item, "text")]
            if texts:
                set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, texts)
        set_span_data_from_sources(
            span, response, PII_CHAT_RESPONSE_SOURCES, require_truthy=True
        )
    set_span_data_from_sources(
        span, response, CHAT_RESPONSE_SOURCES, require_truthy=False
    )
    usage = get_first_from_sources(response, CHAT_USAGE_SOURCES)
    if usage is not None:
        _record_token_usage_v2(span, usage)


def _record_token_usage_v2(span, usage):
    # type: (Span, Any) -> None
    usage_obj = get_first_from_sources(usage, USAGE_UNIT_SOURCES)
    if usage_obj is None:
        return
    record_token_usage(
        span,
        input_tokens=get_first_from_sources(
            usage_obj, USAGE_TOKEN_SOURCES[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS]
        ),
        output_tokens=get_first_from_sources(
            usage_obj, USAGE_TOKEN_SOURCES[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS]
        ),
    )
