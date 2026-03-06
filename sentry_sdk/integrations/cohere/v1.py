import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import set_data_normalized, normalize_message_roles
from sentry_sdk.consts import OP, SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from sentry_sdk.tracing import Span
    from cohere import StreamedChatResponse

import sentry_sdk
from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    _capture_exception,
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


COLLECTED_CHAT_PARAMS = {
    "model": SPANDATA.GEN_AI_REQUEST_MODEL,
    "k": SPANDATA.GEN_AI_REQUEST_TOP_K,
    "p": SPANDATA.GEN_AI_REQUEST_TOP_P,
    "seed": SPANDATA.GEN_AI_REQUEST_SEED,
    "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
}

COLLECTED_PII_CHAT_PARAMS = {
    "tools": SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
    "preamble": SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
}

COLLECTED_CHAT_RESP_ATTRS = {
    "generation_id": SPANDATA.GEN_AI_RESPONSE_ID,
    "finish_reason": SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
}

COLLECTED_PII_CHAT_RESP_ATTRS = {
    "tool_calls": SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
}


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


def _collect_chat_response_fields(span, res, include_pii):
    # type: (Span, Any, bool) -> None
    if include_pii:
        if hasattr(res, "text"):
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, [res.text])
        for attr, spandata_key in COLLECTED_PII_CHAT_RESP_ATTRS.items():
            if hasattr(res, attr):
                set_data_normalized(span, spandata_key, getattr(res, attr))

    for attr, spandata_key in COLLECTED_CHAT_RESP_ATTRS.items():
        if hasattr(res, attr):
            set_data_normalized(span, spandata_key, getattr(res, attr))

    if hasattr(res, "meta"):
        if hasattr(res.meta, "billed_units"):
            record_token_usage(
                span,
                input_tokens=res.meta.billed_units.input_tokens,
                output_tokens=res.meta.billed_units.output_tokens,
            )
        elif hasattr(res.meta, "tokens"):
            record_token_usage(
                span,
                input_tokens=res.meta.tokens.input_tokens,
                output_tokens=res.meta.tokens.output_tokens,
            )


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

        message = kwargs.get("message")
        model = kwargs.get("model", "")
        include_pii = should_send_default_pii() and integration.include_prompts

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
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_STREAMING, streaming)

            if include_pii:
                messages = _extract_v1_messages(kwargs)
                messages = normalize_message_roles(messages)
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    messages,
                    unpack=False,
                )
                for k, v in COLLECTED_PII_CHAT_PARAMS.items():
                    if k in kwargs:
                        set_data_normalized(span, v, kwargs[k])

            for k, v in COLLECTED_CHAT_PARAMS.items():
                if k in kwargs:
                    set_data_normalized(span, v, kwargs[k])

            if streaming:
                return _iter_stream_events(res, span, include_pii)
            elif isinstance(res, NonStreamedChatResponse):
                _collect_chat_response_fields(span, res, include_pii)
                span.__exit__(None, None, None)
            else:
                set_data_normalized(span, "unknown_response", True)
                span.__exit__(None, None, None)
            return res

    return new_chat


def _iter_stream_events(old_iterator, span, include_pii):
    # type: (Any, Span, bool) -> Iterator[StreamedChatResponse]
    for x in old_iterator:
        with capture_internal_exceptions():
            if isinstance(x, ChatStreamEndEvent) or isinstance(
                x, StreamEndStreamedChatResponse
            ):
                _collect_chat_response_fields(span, x.response, include_pii)
        yield x

    span.__exit__(None, None, None)


def _extract_v1_messages(kwargs):
    # type: (Any) -> list[dict[str, str]]
    messages = []
    for x in kwargs.get("chat_history", []):
        messages.append(
            {
                "role": getattr(x, "role", "").lower(),
                "content": getattr(x, "message", ""),
            }
        )
    message = kwargs.get("message")
    if message:
        messages.append({"role": "user", "content": message})
    return messages


def _extract_v1_response_text(response):
    # type: (Any) -> list[str] | None
    text = getattr(response, "text", None)
    return [text] if text is not None else None
