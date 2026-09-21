from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import (
    get_start_span_function,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
)

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    from mistralai.client.chat import Chat
    from mistralai.client.models import ChatCompletionResponse
except ImportError:
    raise DidNotEnable("mistralai not installed")


class MistralIntegration(Integration):
    identifier = "mistral"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once() -> None:
        Chat.complete = _wrap_complete(Chat.complete)  # type: ignore[method-assign]

        Chat.complete_async = _wrap_complete_async(Chat.complete_async)  # type: ignore[method-assign]


def _wrap_complete(f: "Callable[..., Any]") -> "Callable[..., Any]":
    @wraps(f)
    def wrap_complete(self: "Chat", *args: "Any", **kwargs: "Any") -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(MistralIntegration)
        if integration is None or kwargs.get("stream"):
            return f(self, *args, **kwargs)

        model = kwargs.get("model")

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.start_span(
                name=f"chat {model}" if model is not None else "chat",
                attributes={
                    "sentry.op": OP.GEN_AI_CHAT,
                    "sentry.origin": MistralIntegration.origin,
                    SPANDATA.GEN_AI_PROVIDER_NAME: "mistral",
                    SPANDATA.GEN_AI_OPERATION_NAME: "chat",
                },
            )

            set_on_span = span.set_attribute
        else:
            span = get_start_span_function()(
                op=OP.GEN_AI_CHAT,
                name=f"chat {model}" if model is not None else "chat",
                origin=MistralIntegration.origin,
            )
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "mistral")
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

            set_on_span = span.set_data

        with span:
            if model is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            set_on_span(SPANDATA.GEN_AI_RESPONSE_STREAMING, False)

            response = f(self, *args, **kwargs)

            if not isinstance(response, ChatCompletionResponse):
                return response

            if response.usage.prompt_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, response.usage.prompt_tokens
                )

            if response.usage.completion_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS,
                    response.usage.completion_tokens,
                )

            if response.usage.total_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, response.usage.total_tokens
                )

            return response

    return wrap_complete


def _wrap_complete_async(f: "Callable[..., Any]") -> "Callable[..., Any]":
    @wraps(f)
    async def wrap_complete_async(self: "Chat", *args: "Any", **kwargs: "Any") -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(MistralIntegration)
        if integration is None or kwargs.get("stream"):
            return await f(self, *args, **kwargs)

        model = kwargs.get("model")

        if has_span_streaming_enabled(client.options):
            span = sentry_sdk.traces.start_span(
                name=f"chat {model}" if model is not None else "chat",
                attributes={
                    "sentry.op": OP.GEN_AI_CHAT,
                    "sentry.origin": MistralIntegration.origin,
                    SPANDATA.GEN_AI_PROVIDER_NAME: "mistral",
                    SPANDATA.GEN_AI_OPERATION_NAME: "chat",
                },
            )

            set_on_span = span.set_attribute
        else:
            span = get_start_span_function()(
                op=OP.GEN_AI_CHAT,
                name=f"chat {model}" if model is not None else "chat",
                origin=MistralIntegration.origin,
            )
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "mistral")
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")

            set_on_span = span.set_data

        with span:
            if model is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            set_on_span(SPANDATA.GEN_AI_RESPONSE_STREAMING, False)

            response = await f(self, *args, **kwargs)

            if not isinstance(response, ChatCompletionResponse):
                return response

            if response.usage.prompt_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, response.usage.prompt_tokens
                )

            if response.usage.completion_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS,
                    response.usage.completion_tokens,
                )

            if response.usage.total_tokens is not None:
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, response.usage.total_tokens
                )

            return response

    return wrap_complete_async
