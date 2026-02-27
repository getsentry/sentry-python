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
    """Called from CohereIntegration.setup_once() to patch V1 Client methods."""
    try:
        from cohere.client import Client
        from cohere.base_client import BaseCohere
    except ImportError:
        return

    BaseCohere.chat = _wrap_chat(BaseCohere.chat, streaming=False)
    BaseCohere.chat_stream = _wrap_chat(BaseCohere.chat_stream, streaming=True)
    Client.embed = wrap_embed_fn(Client.embed)


def _wrap_chat(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]

    try:
        from cohere import (
            ChatStreamEndEvent,
            NonStreamedChatResponse,
        )

        if TYPE_CHECKING:
            from cohere import StreamedChatResponse
    except ImportError:
        return f

    try:
        # cohere 5.9.3+
        from cohere import StreamEndStreamedChatResponse
    except ImportError:
        from cohere import (
            StreamedChatResponse_StreamEnd as StreamEndStreamedChatResponse,
        )

    def collect_chat_response_fields(span, res, include_pii):
        # type: (Span, NonStreamedChatResponse, bool) -> None
        if include_pii:
            if hasattr(res, "text"):
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    [res.text],
                )
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
                set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)

            if should_send_default_pii() and integration.include_prompts:
                messages = []
                for x in kwargs.get("chat_history", []):
                    messages.append(
                        {
                            "role": getattr(x, "role", "").lower(),
                            "content": getattr(x, "message", ""),
                        }
                    )
                messages.append({"role": "user", "content": message})
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
                for k, v in COLLECTED_PII_CHAT_PARAMS.items():
                    if k in kwargs:
                        set_data_normalized(span, v, kwargs[k])

            for k, v in COLLECTED_CHAT_PARAMS.items():
                if k in kwargs:
                    set_data_normalized(span, v, kwargs[k])
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_STREAMING, streaming)

            if streaming:
                old_iterator = res

                def new_iterator():
                    # type: () -> Iterator[StreamedChatResponse]
                    with capture_internal_exceptions():
                        for x in old_iterator:
                            if isinstance(x, ChatStreamEndEvent) or isinstance(
                                x, StreamEndStreamedChatResponse
                            ):
                                collect_chat_response_fields(
                                    span,
                                    x.response,
                                    include_pii=should_send_default_pii()
                                    and integration.include_prompts,
                                )
                            yield x

                    span.__exit__(None, None, None)

                return new_iterator()
            elif isinstance(res, NonStreamedChatResponse):
                collect_chat_response_fields(
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
