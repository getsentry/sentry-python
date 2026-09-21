import json
from collections.abc import Sequence
from functools import wraps
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.ai.utils import (
    get_start_span_function,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import (
    has_span_streaming_enabled,
)
from sentry_sdk.utils import has_data_collection_enabled

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Optional, TypeGuard, Union

    from mistralai.client.models import (
        AssistantMessageTypedDict,
        ChatCompletionRequestMessage,
        ChatCompletionRequestMessageTypedDict,
        SystemMessageTypedDict,
        TextChunkTypedDict,
    )

    from sentry_sdk._types import InputMessage, OutputMessage, TextPart

try:
    from mistralai.client.chat import Chat
    from mistralai.client.models import (
        AssistantMessage,
        ChatCompletionResponse,
        SystemMessage,
        TextChunk,
        UserMessage,
    )
except ImportError:
    raise DidNotEnable("mistralai not installed")


class MistralIntegration(Integration):
    identifier = "mistral"
    origin = f"auto.ai.{identifier}"

    @staticmethod
    def setup_once() -> None:
        Chat.complete = _wrap_complete(Chat.complete)  # type: ignore[method-assign]

        Chat.complete_async = _wrap_complete_async(Chat.complete_async)  # type: ignore[method-assign]


def _is_system_instruction(
    message: "Union[ChatCompletionRequestMessage, ChatCompletionRequestMessageTypedDict]",
) -> "TypeGuard[Union[SystemMessage, SystemMessageTypedDict]]":
    if isinstance(message, SystemMessage):
        return True

    if isinstance(message, dict):
        return message.get("role") == "system"

    return False


def _transform_input_messages(
    messages: "Sequence[Union[ChatCompletionRequestMessage, ChatCompletionRequestMessageTypedDict]]",
) -> "list[InputMessage]":
    input_messages: "list[InputMessage]" = []

    for message in messages:
        if isinstance(message, UserMessage) and isinstance(message.content, str):
            input_messages.append(
                {
                    "role": "user",
                    "parts": [{"type": "text", "content": message.content}],
                }
            )
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            text_parts = [
                part for part in message.content if isinstance(part, TextChunk)
            ]
            input_messages.append(
                {
                    "role": "user",
                    "parts": [
                        {"type": "text", "content": part.text} for part in text_parts
                    ],
                }
            )

        if isinstance(message, AssistantMessage) and isinstance(message.content, str):
            input_messages.append(
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": message.content}],
                }
            )
        elif isinstance(message, AssistantMessage) and isinstance(
            message.content, list
        ):
            text_parts = [
                part for part in message.content if isinstance(part, TextChunk)
            ]
            input_messages.append(
                {
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "content": part.text} for part in text_parts
                    ],
                }
            )

        if not isinstance(message, dict):
            continue

        role = message.get("role")
        if role != "user" and role != "assistant":
            continue

        content = message.get("content")
        if isinstance(content, str):
            input_messages.append(
                {"role": role, "parts": [{"type": "text", "content": content}]}
            )

        if not isinstance(content, list):
            continue

        text_parts = [
            part
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and "text" in part
        ]
        input_messages.append(
            {
                "role": role,
                "parts": [
                    {
                        "type": "text",
                        "content": cast("TextChunkTypedDict", part)["text"],
                    }
                    for part in text_parts
                ],
            }
        )

    return input_messages


def _transform_output_message(
    message: "Union[AssistantMessage, AssistantMessageTypedDict]",
) -> "Optional[OutputMessage]":
    if isinstance(message, AssistantMessage):
        if message.content is None:
            return None

        if isinstance(message.content, str):
            return {
                "role": "assistant",
                "parts": [{"type": "text", "content": message.content}],
            }

        parts = [part for part in message.content if isinstance(part, TextChunk)]
        return {
            "role": "assistant",
            "parts": [{"type": "text", "content": part.text} for part in parts],
        }

    content = message.get("content")
    if content is None:
        return None

    if isinstance(content, str):
        return {"role": "assistant", "parts": [{"type": "text", "content": content}]}

    text_parts = [
        part
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    return {
        "role": "assistant",
        "parts": [
            {"type": "text", "content": cast("TextChunkTypedDict", part)["text"]}
            for part in text_parts
        ],
    }


def _transform_system_instructions(
    messages: "list[Union[SystemMessage, SystemMessageTypedDict]]",
) -> "list[TextPart]":
    system_instructions: "list[TextPart]" = []
    for message in messages:
        if isinstance(message, SystemMessage) and isinstance(message.content, str):
            system_instructions.append({"type": "text", "content": message.content})
        elif isinstance(message, SystemMessage) and isinstance(message.content, list):
            for part in message.content:
                if not isinstance(part, TextChunk):
                    continue
                system_instructions.append({"type": "text", "content": part.text})

        if not isinstance(message, dict):
            continue

        content = message.get("content")
        if isinstance(content, str):
            system_instructions.append({"type": "text", "content": content})

        if not isinstance(content, list):
            continue

        for part in content:
            if (
                not isinstance(part, dict)
                or part.get("type") != "text"
                or "text" not in part
            ):
                continue

            text = cast("TextChunkTypedDict", part)["text"]
            system_instructions.append({"type": "text", "content": text})

    return system_instructions


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

            messages = kwargs.get("messages")
            if isinstance(messages, Sequence) and (
                (
                    has_data_collection_enabled(client.options)
                    and client.options["data_collection"]["gen_ai"]["inputs"]
                )
                or (
                    not has_data_collection_enabled(client.options)
                    and should_send_default_pii()
                )
            ):
                system_instructions = [
                    message for message in messages if _is_system_instruction(message)
                ]
                if len(system_instructions) > 0:
                    set_on_span(
                        SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                        json.dumps(_transform_system_instructions(system_instructions)),
                    )

                set_on_span(
                    SPANDATA.GEN_AI_INPUT_MESSAGES,
                    json.dumps(_transform_input_messages(messages)),
                )

            max_tokens = kwargs.get("max_tokens")
            if max_tokens is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)

            frequency_penalty = kwargs.get("frequency_penalty")
            if frequency_penalty is not None:
                set_on_span(
                    SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY, frequency_penalty
                )

            presence_penalty = kwargs.get("presence_penalty")
            if presence_penalty is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY, presence_penalty)

            temperature = kwargs.get("temperature")
            if temperature is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_TEMPERATURE, temperature)

            top_p = kwargs.get("top_p")
            if top_p is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_TOP_P, top_p)

            reasoning_effort = kwargs.get("reasoning_effort")
            if reasoning_effort is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL, reasoning_effort)

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

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["outputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                output_messages: "list[OutputMessage]" = []
                for choice in response.choices:
                    if choice.message is None:
                        continue

                    transformed_message = _transform_output_message(choice.message)
                    if transformed_message is None:
                        continue

                    output_messages.append(transformed_message)

                set_on_span(
                    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(output_messages),
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

            messages: "Optional[Union[Iterable[ChatCompletionRequestMessage], Iterable[ChatCompletionRequestMessageTypedDict]]]" = kwargs.get(
                "messages"
            )
            if isinstance(messages, Sequence) and (
                (
                    has_data_collection_enabled(client.options)
                    and client.options["data_collection"]["gen_ai"]["inputs"]
                )
                or (
                    not has_data_collection_enabled(client.options)
                    and should_send_default_pii()
                )
            ):
                system_instructions = [
                    message for message in messages if _is_system_instruction(message)
                ]
                if len(system_instructions) > 0:
                    set_on_span(
                        SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                        json.dumps(_transform_system_instructions(system_instructions)),
                    )

                set_on_span(
                    SPANDATA.GEN_AI_INPUT_MESSAGES,
                    json.dumps(_transform_input_messages(messages)),
                )

            max_tokens = kwargs.get("max_tokens")
            if max_tokens is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)

            frequency_penalty = kwargs.get("frequency_penalty")
            if frequency_penalty is not None:
                set_on_span(
                    SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY, frequency_penalty
                )

            presence_penalty = kwargs.get("presence_penalty")
            if presence_penalty is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY, presence_penalty)

            temperature = kwargs.get("temperature")
            if temperature is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_TEMPERATURE, temperature)

            top_p = kwargs.get("top_p")
            if top_p is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_TOP_P, top_p)

            reasoning_effort = kwargs.get("reasoning_effort")
            if reasoning_effort is not None:
                set_on_span(SPANDATA.GEN_AI_REQUEST_REASONING_LEVEL, reasoning_effort)

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

            if (
                has_data_collection_enabled(client.options)
                and client.options["data_collection"]["gen_ai"]["outputs"]
            ) or (
                not has_data_collection_enabled(client.options)
                and should_send_default_pii()
            ):
                output_messages: "list[OutputMessage]" = []
                for choice in response.choices:
                    if choice.message is None:
                        continue

                    transformed_message = _transform_output_message(choice.message)
                    if transformed_message is None:
                        continue

                    output_messages.append(transformed_message)

                set_on_span(
                    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(output_messages),
                )

            return response

    return wrap_complete_async
