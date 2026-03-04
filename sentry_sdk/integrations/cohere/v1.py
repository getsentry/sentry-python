import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.span_config import set_input_span_data
from sentry_sdk.ai.utils import set_data_normalized, transform_message_content
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from cohere import StreamedChatResponse

import sentry_sdk
from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    _capture_exception,
)
from sentry_sdk.integrations.cohere.utils import (
    get_first_from_sources,
    set_span_data_from_sources,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, reraise

try:
    from cohere import ChatStreamEndEvent, NonStreamedChatResponse

    try:
        from cohere import StreamEndStreamedChatResponse
    except ImportError:
        from cohere import (
            StreamedChatResponse_StreamEnd as StreamEndStreamedChatResponse,
        )

    _has_chat_types = True
except ImportError:
    _has_chat_types = False

CHAT_RESPONSE_SOURCES = {
    SPANDATA.GEN_AI_RESPONSE_ID: [("generation_id",)],
    SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS: [("finish_reason",)],
}
PII_CHAT_RESPONSE_SOURCES = {
    SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS: [("tool_calls",)],
}
CHAT_RESPONSE_TEXT_SOURCES = [("text",)]
CHAT_USAGE_TOKEN_SOURCES = {
    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: [
        ("meta", "billed_units", "input_tokens"),
        ("meta", "tokens", "input_tokens"),
    ],
    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: [
        ("meta", "billed_units", "output_tokens"),
        ("meta", "tokens", "output_tokens"),
    ],
}
STREAM_RESPONSE_SOURCES = [("response",)]


def setup_v1(wrap_embed_fn):
    # type: (Callable[..., Any]) -> None
    try:
        from cohere.base_client import BaseCohere
        from cohere.client import Client
    except ImportError:
        return

    BaseCohere.chat = _wrap_chat(BaseCohere.chat, streaming=False)
    BaseCohere.chat_stream = _wrap_chat(BaseCohere.chat_stream, streaming=True)
    Client.embed = wrap_embed_fn(Client.embed)


def _wrap_chat(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    if not _has_chat_types:
        return f

    @wraps(f)
    def new_chat(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if (
            integration is None
            or "message" not in kwargs
            or not isinstance(kwargs.get("message"), str)
        ):
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
                if model:
                    set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)
                set_input_span_data(
                    span,
                    kwargs,
                    integration,
                    {
                        "system": "cohere",
                        "operation": "chat",
                        "extract_messages": _extract_messages_v1,
                        "extra_static": {SPANDATA.GEN_AI_RESPONSE_STREAMING: streaming},
                    },
                )

                if streaming:
                    return _iter_v1_stream_events(res, span, include_pii)
                if isinstance(res, NonStreamedChatResponse):
                    _collect_v1_response_fields(span, res, include_pii=include_pii)
                else:
                    set_data_normalized(span, "unknown_response", True)
                return res

    return new_chat


def _extract_messages_v1(kwargs):
    # type: (dict[str, Any]) -> list[dict[str, str]]
    messages = []
    for x in kwargs.get("chat_history", []):
        messages.append(
            {
                "role": getattr(x, "role", "").lower(),
                "content": transform_message_content(getattr(x, "message", "")),
            }
        )
    message = kwargs.get("message")
    if message:
        messages.append({"role": "user", "content": transform_message_content(message)})
    return messages


def _iter_v1_stream_events(old_iterator, span, include_pii):
    # type: (Any, Any, bool) -> Iterator[StreamedChatResponse]
    with capture_internal_exceptions():
        for x in old_iterator:
            if isinstance(x, ChatStreamEndEvent) or isinstance(
                x, StreamEndStreamedChatResponse
            ):
                _collect_v1_stream_end_fields(span, x, include_pii)
            yield x


def _collect_v1_stream_end_fields(span, event, include_pii):
    # type: (Any, Any, bool) -> None
    response = get_first_from_sources(event, STREAM_RESPONSE_SOURCES)
    if response is not None:
        _collect_v1_response_fields(span, response, include_pii)


def _collect_v1_response_fields(span, response, include_pii):
    # type: (Any, Any, bool) -> None
    if include_pii:
        text = get_first_from_sources(response, CHAT_RESPONSE_TEXT_SOURCES)
        if text is not None:
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, [text])
        set_span_data_from_sources(
            span, response, PII_CHAT_RESPONSE_SOURCES, require_truthy=False
        )

    set_span_data_from_sources(
        span, response, CHAT_RESPONSE_SOURCES, require_truthy=False
    )
    record_token_usage(
        span,
        input_tokens=get_first_from_sources(
            response, CHAT_USAGE_TOKEN_SOURCES[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS]
        ),
        output_tokens=get_first_from_sources(
            response, CHAT_USAGE_TOKEN_SOURCES[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS]
        ),
    )
