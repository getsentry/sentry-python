import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.ai.utils import truncate_and_annotate_messages
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3.consts import AI_ORIGIN, IDENTIFIER
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import should_truncate_gen_ai_input
from sentry_sdk.utils import capture_internal_exceptions, has_data_collection_enabled

if TYPE_CHECKING:
    from typing import Any, Literal, Optional, Union

    from sentry_sdk._types import (
        Attributes,
        InputMessage,
        OutputMessage,
        ReasoningPart,
        TextPart,
        ToolCallPart,
        ToolCallResponsePart,
        ToolDefinition,
    )
    from sentry_sdk.integrations.boto3 import Boto3Integration
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.traces import StreamedSpan
    from sentry_sdk.tracing import Span

    ContentPart = Union[TextPart, ReasoningPart, ToolCallPart, ToolCallResponsePart]


# `gen_ai.provider.name` of Amazon Bedrock.
# https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/#gen-ai-provider-name
PROVIDER_NAME = "aws.bedrock"

# `gen_ai.operation.name` and span op of each instrumented operation.
GEN_AI_OPERATIONS = {
    "Converse": ("chat", OP.GEN_AI_CHAT),
}


class _BedrockRuntimeExtension(_ServiceExtension):
    """
    Turns Bedrock Runtime model calls into `gen_ai` client spans.
    https://develop.sentry.dev/sdk/telemetry/traces/modules/ai-agents/#ai-client-span
    """

    __slots__ = ()

    def get_span_name(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return None

        operation_name, _ = GEN_AI_OPERATIONS[ctx.operation_name]
        model_id = ctx.params.get("modelId")
        return f"{operation_name} {model_id}" if model_id else operation_name

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return None

        _, span_op = GEN_AI_OPERATIONS[ctx.operation_name]
        return span_op

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return None

        return AI_ORIGIN

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return {}

        operation_name, _ = GEN_AI_OPERATIONS[ctx.operation_name]
        attributes: "Attributes" = {
            SPANDATA.GEN_AI_PROVIDER_NAME: PROVIDER_NAME,
            SPANDATA.GEN_AI_OPERATION_NAME: operation_name,
            SPANDATA.GEN_AI_RESPONSE_STREAMING: False,
        }

        model_id = ctx.params.get("modelId")
        if model_id:
            attributes[SPANDATA.GEN_AI_REQUEST_MODEL] = model_id

        return attributes

    def get_span_start_attributes(
        self, ctx: "AwsCallContext", span: "Union[Span, StreamedSpan]"
    ) -> "Attributes":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return {}

        return _get_converse_request_attributes(span, ctx.params)

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        if ctx.operation_name not in GEN_AI_OPERATIONS:
            return {}

        return _get_converse_response_attributes(response)


def _should_record_gen_ai(kind: 'Literal["inputs", "outputs"]') -> bool:
    """
    Record prompts (`inputs`) or responses (`outputs`) as `data_collection` allows,
    or, if it isn't set, with `send_default_pii` and `include_prompts`, like the
    other gen_ai integrations.
    """
    client = sentry_sdk.get_client()
    if has_data_collection_enabled(client.options):
        return bool(client.options["data_collection"]["gen_ai"][kind])

    integration = cast("Optional[Boto3Integration]", client.get_integration(IDENTIFIER))
    return (
        should_send_default_pii()
        and integration is not None
        and integration.include_prompts
    )


def _get_converse_request_attributes(
    span: "Union[Span, StreamedSpan]", params: "Mapping[str, Any]"
) -> "Attributes":
    """
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html#API_runtime_Converse_RequestSyntax
    """
    attributes: "Attributes" = {}

    inference_config = params.get("inferenceConfig")
    if isinstance(inference_config, Mapping):
        if inference_config.get("maxTokens") is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] = inference_config[
                "maxTokens"
            ]
        if inference_config.get("temperature") is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_TEMPERATURE] = inference_config[
                "temperature"
            ]
        if inference_config.get("topP") is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_TOP_P] = inference_config["topP"]
        if isinstance(inference_config.get("stopSequences"), (list, tuple)):
            attributes[SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES] = list(
                inference_config["stopSequences"]
            )

    if not _should_record_gen_ai("inputs"):
        return attributes

    with capture_internal_exceptions():
        system_instructions = _transform_system_instructions(params.get("system"))
        if system_instructions:
            attributes[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS] = json.dumps(
                system_instructions
            )

    with capture_internal_exceptions():
        attributes.update(
            _get_input_messages_attributes(
                span, _transform_input_messages(params.get("messages"))
            )
        )

    with capture_internal_exceptions():
        tool_config = params.get("toolConfig")
        if isinstance(tool_config, Mapping):
            tool_definitions = _transform_tool_definitions(tool_config.get("tools"))
            if tool_definitions:
                attributes[SPANDATA.GEN_AI_TOOL_DEFINITIONS] = json.dumps(
                    tool_definitions
                )

    return attributes


def _get_input_messages_attributes(
    span: "Union[Span, StreamedSpan]", input_messages: "list[InputMessage]"
) -> "Attributes":
    """
    Like the other `gen_ai` integrations, only the last message is kept when
    `gen_ai` spans are sent in transactions, so that long histories don't exceed
    the transaction size limit.
    """
    messages: "Optional[list[dict[str, Any]]]" = cast(
        "list[dict[str, Any]]", input_messages
    )
    if should_truncate_gen_ai_input(sentry_sdk.get_client().options):
        messages = truncate_and_annotate_messages(
            messages, span, sentry_sdk.get_current_scope()
        )

    if not messages:
        return {}

    return {SPANDATA.GEN_AI_INPUT_MESSAGES: json.dumps(messages)}


def _get_converse_response_attributes(response: "Any") -> "Attributes":
    """
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html#API_runtime_Converse_ResponseSyntax
    """
    attributes: "Attributes" = {}
    if not isinstance(response, Mapping):
        return attributes

    stop_reason = response.get("stopReason")
    if isinstance(stop_reason, str):
        attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] = [stop_reason]

    usage = response.get("usage")
    if isinstance(usage, Mapping):
        attributes.update(_get_usage_attributes(usage))

    if not _should_record_gen_ai("outputs"):
        return attributes

    with capture_internal_exceptions():
        output = response.get("output")
        message = output.get("message") if isinstance(output, Mapping) else None
        if isinstance(message, Mapping):
            output_message: "OutputMessage" = {"role": "assistant", "parts": []}
            for part in _transform_content_blocks(message.get("content")):
                # models don't respond with tool results.
                if part["type"] != "tool_call_response":
                    output_message["parts"].append(part)
            attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES] = json.dumps([output_message])

    return attributes


def _get_usage_attributes(usage: "Mapping[str, Any]") -> "Attributes":
    """
    Bedrock's `inputTokens` excludes the tokens read from or written to the prompt
    cache, but `gen_ai.usage.input_tokens` includes them.
    https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
    """
    attributes: "Attributes" = {}

    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    total_tokens = usage.get("totalTokens")
    cache_read_input_tokens = usage.get("cacheReadInputTokens")
    cache_write_input_tokens = usage.get("cacheWriteInputTokens")

    if cache_read_input_tokens is not None:
        attributes[SPANDATA.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS] = (
            cache_read_input_tokens
        )
    if cache_write_input_tokens is not None:
        attributes[SPANDATA.GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS] = (
            cache_write_input_tokens
        )
    if input_tokens is not None:
        attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS] = (
            input_tokens
            + (cache_read_input_tokens or 0)
            + (cache_write_input_tokens or 0)
        )
    if output_tokens is not None:
        attributes[SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS] = output_tokens
    if total_tokens is not None:
        attributes[SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS] = total_tokens

    return attributes


def _transform_system_instructions(system: "Any") -> "list[TextPart]":
    if not isinstance(system, (list, tuple)):
        return []

    return [
        {"type": "text", "content": block["text"]}
        for block in system
        if isinstance(block, Mapping) and isinstance(block.get("text"), str)
    ]


def _transform_input_messages(messages: "Any") -> "list[InputMessage]":
    """
    Bedrock sends tool results in `user` messages; they're recorded as `tool`
    messages, which is how `gen_ai.input.messages` represents them.
    """
    if not isinstance(messages, (list, tuple)):
        return []

    input_messages: "list[InputMessage]" = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue

        role = message.get("role")
        if role != "user" and role != "assistant":
            continue

        parts: "list[ContentPart]" = []
        tool_parts: "list[ContentPart]" = []
        for part in _transform_content_blocks(message.get("content")):
            if part["type"] == "tool_call_response":
                tool_parts.append(part)
            else:
                parts.append(part)

        if tool_parts:
            input_messages.append({"role": "tool", "parts": tool_parts})
        if parts:
            input_messages.append({"role": role, "parts": parts})

    return input_messages


def _transform_content_blocks(content: "Any") -> "list[ContentPart]":
    """
    Transform text, reasoning, tool use, and tool result content blocks. Media
    content blocks, e.g. images and documents, are not recorded.
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ContentBlock.html
    """
    if not isinstance(content, (list, tuple)):
        return []

    parts: "list[ContentPart]" = []
    for block in content:
        if not isinstance(block, Mapping):
            continue

        if isinstance(block.get("text"), str):
            parts.append({"type": "text", "content": block["text"]})
            continue

        reasoning = block.get("reasoningContent")
        if isinstance(reasoning, Mapping):
            reasoning_text = reasoning.get("reasoningText")
            if isinstance(reasoning_text, Mapping) and isinstance(
                reasoning_text.get("text"), str
            ):
                parts.append({"type": "reasoning", "content": reasoning_text["text"]})
            continue

        tool_use = block.get("toolUse")
        if isinstance(tool_use, Mapping):
            tool_call: "ToolCallPart" = {"type": "tool_call"}
            if "toolUseId" in tool_use:
                tool_call["id"] = tool_use["toolUseId"]
            if "name" in tool_use:
                tool_call["name"] = tool_use["name"]
            if "input" in tool_use:
                tool_call["arguments"] = tool_use["input"]
            parts.append(tool_call)
            continue

        tool_result = block.get("toolResult")
        if isinstance(tool_result, Mapping):
            tool_response: "ToolCallResponsePart" = {"type": "tool_call_response"}
            if "toolUseId" in tool_result:
                tool_response["id"] = tool_result["toolUseId"]

            result = [
                item["text"] if "text" in item else item["json"]
                for item in tool_result.get("content") or ()
                if isinstance(item, Mapping) and ("text" in item or "json" in item)
            ]
            if result:
                tool_response["result"] = result[0] if len(result) == 1 else result
            parts.append(tool_response)

    return parts


def _transform_tool_definitions(tools: "Any") -> "list[ToolDefinition]":
    """
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ToolSpecification.html
    """
    if not isinstance(tools, (list, tuple)):
        return []

    tool_definitions: "list[ToolDefinition]" = []
    for tool in tools:
        tool_spec = tool.get("toolSpec") if isinstance(tool, Mapping) else None
        if not isinstance(tool_spec, Mapping):
            continue

        tool_definition: "ToolDefinition" = {"type": "function"}
        if "name" in tool_spec:
            tool_definition["name"] = tool_spec["name"]
        if "description" in tool_spec:
            tool_definition["description"] = tool_spec["description"]

        input_schema = tool_spec.get("inputSchema")
        if isinstance(input_schema, Mapping) and isinstance(
            input_schema.get("json"), Mapping
        ):
            tool_definition["parameters"] = dict(input_schema["json"])
        tool_definitions.append(tool_definition)

    return tool_definitions
