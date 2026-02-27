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

from sentry_sdk.tracing_utils import set_span_errored

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception, reraise

try:
    from cohere.client import Client
    from cohere.base_client import BaseCohere
    from cohere import (
        ChatStreamEndEvent,
        NonStreamedChatResponse,
    )

    if TYPE_CHECKING:
        from cohere import StreamedChatResponse
except ImportError:
    raise DidNotEnable("Cohere not installed")

try:
    # cohere 5.9.3+
    from cohere import StreamEndStreamedChatResponse
except ImportError:
    from cohere import StreamedChatResponse_StreamEnd as StreamEndStreamedChatResponse

COLLECTED_CHAT_PARAMS = {
    "model": SPANDATA.GEN_AI_REQUEST_MODEL,
    "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
    "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
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


class CohereIntegration(Integration):
    identifier = "cohere"
    origin = f"auto.ai.{identifier}"

    def __init__(self: "CohereIntegration", include_prompts: bool = True) -> None:
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        BaseCohere.chat = _wrap_chat(BaseCohere.chat, streaming=False)
        Client.embed = _wrap_embed(Client.embed)
        BaseCohere.chat_stream = _wrap_chat(BaseCohere.chat_stream, streaming=True)

        from sentry_sdk.integrations.cohere_v2 import setup_v2

        setup_v2(_wrap_embed)


def _capture_exception(exc: "Any") -> None:
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "cohere", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _wrap_chat(f: "Callable[..., Any]", streaming: bool) -> "Callable[..., Any]":
    def collect_chat_response_fields(
        span: "Span", res: "NonStreamedChatResponse", include_pii: bool
    ) -> None:
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
    def new_chat(*args: "Any", **kwargs: "Any") -> "Any":
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
<<<<<<< HEAD
                    messages.append(
                        {
                            "role": getattr(x, "role", "").lower(),
=======
                    role = getattr(x, "role", "").lower()
                    if role == "chatbot":
                        role = "assistant"
                    messages.append(
                        {
                            "role": role,
>>>>>>> c51eeb90 (correct model)
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

                def new_iterator() -> "Iterator[StreamedChatResponse]":
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


def _wrap_embed(f: "Callable[..., Any]") -> "Callable[..., Any]":
    @wraps(f)
    def new_embed(*args: "Any", **kwargs: "Any") -> "Any":
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if integration is None:
            return f(*args, **kwargs)

        model = kwargs.get("model", "")

        with sentry_sdk.start_span(
            op=OP.GEN_AI_EMBEDDINGS,
            name=f"embeddings {model}".strip(),
            origin=CohereIntegration.origin,
        ) as span:
            set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "cohere")
            set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "embeddings")

            if "texts" in kwargs and (
                should_send_default_pii() and integration.include_prompts
            ):
                if isinstance(kwargs["texts"], str):
                    set_data_normalized(
                        span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, [kwargs["texts"]]
                    )
                elif (
                    isinstance(kwargs["texts"], list)
                    and len(kwargs["texts"]) > 0
                    and isinstance(kwargs["texts"][0], str)
                ):
                    set_data_normalized(
                        span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, kwargs["texts"]
                    )

            if "model" in kwargs:
                set_data_normalized(
                    span, SPANDATA.GEN_AI_REQUEST_MODEL, kwargs["model"]
                )
            try:
                res = f(*args, **kwargs)
            except Exception as e:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    _capture_exception(e)
                reraise(*exc_info)
            if (
                hasattr(res, "meta")
                and hasattr(res.meta, "billed_units")
                and hasattr(res.meta.billed_units, "input_tokens")
            ):
                record_token_usage(
                    span,
                    input_tokens=res.meta.billed_units.input_tokens,
                    total_tokens=res.meta.billed_units.input_tokens,
                )
            return res

    return new_embed
