import json
import sys
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.tracing import SPANSTATUS
from sentry_sdk.utils import (
    capture_internal_exceptions,
    has_data_collection_enabled,
    reraise,
    should_be_treated_as_error,
)

try:
    from botocore.eventstream import EventStream
except ImportError:
    raise DidNotEnable("botocore is not installed")


if TYPE_CHECKING:
    from typing import Any, Iterator, Mapping, Optional, Union

    from mypy_boto3_bedrock_runtime.type_defs import (
        ConverseResponseTypeDef,
        ConverseStreamResponseTypeDef,
    )

    from sentry_sdk._types import (
        Attributes,
        BlobPart,
        InputMessage,
        ReasoningPart,
        TextPart,
        ToolCallPart,
        ToolCallResponsePart,
        ToolDefinition,
        UriPart,
    )
    from sentry_sdk.integrations.boto3._context import AwsCallContext
    from sentry_sdk.tracing import Span


def _finish_span(
    stream: "EventStream[Any]",
    exc_type: "Optional[type[BaseException]]",
    exc_val: "Optional[BaseException]",
    exc_tb: "Optional[Any]",
) -> None:
    """
    Finishes the span attached to the stream.
    Is a no-op if the stream has no span set, i.e., when the span has already been finished.
    """
    if not hasattr(stream, "_span"):
        return

    span = stream._span
    del stream._span

    if exc_type is not None and should_be_treated_as_error(exc_type, exc_val):
        if isinstance(span, StreamedSpan):
            span.status = SpanStatus.ERROR
        else:
            span.status = SPANSTATUS.INTERNAL_ERROR

    if isinstance(span, StreamedSpan):
        span.end()
    else:
        span.finish()


def _patch_event_stream() -> None:
    original_iter = EventStream.__iter__
    original_close = EventStream.close

    def sentry_patched_iter(
        self: "EventStream[Any]",
    ) -> "Iterator[Any]":
        """
        Sets information received while iterating the response stream on the AI Client Span.
        Responsible for closing the AI Client Span unless the span has already been closed in the close() patch.
        """
        span = getattr(self, "_span", None)
        if span is None:
            yield from original_iter(self)
            return

        set_on_span = (
            span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
        )

        role: "Optional[str]" = None
        parts: "dict[int, Union[TextPart, ReasoningPart, ToolCallPart, ToolCallResponsePart, UriPart, BlobPart]]" = {}

        try:
            for event in original_iter(self):
                if "metadata" in event:
                    usage = event["metadata"]["usage"]

                    input_tokens = usage["inputTokens"]
                    set_on_span(
                        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"]
                    )
                    set_on_span(
                        SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"]
                    )

                    cache_read_tokens = usage.get("cacheReadInputTokens")
                    if cache_read_tokens is not None:
                        input_tokens += cache_read_tokens
                        set_on_span(
                            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                        )

                    cache_write_tokens = usage.get("cacheWriteInputTokens")
                    if cache_write_tokens is not None:
                        input_tokens += cache_write_tokens
                        set_on_span(
                            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE,
                            cache_write_tokens,
                        )

                    set_on_span(
                        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
                        input_tokens,
                    )

                    yield event
                    continue

                client = sentry_sdk.get_client()
                if (
                    not has_data_collection_enabled(client.options)
                    or not client.options["data_collection"]["gen_ai"]["outputs"]
                ) and (
                    has_data_collection_enabled(client.options)
                    or not should_send_default_pii()
                ):
                    yield event
                    continue

                if "messageStart" in event:
                    role = event["messageStart"]["role"]

                    yield event
                    continue

                if "messageStop" in event and role is not None:
                    set_on_span(
                        SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                        json.dumps(
                            [
                                {
                                    "role": role,
                                    "parts": [parts[index] for index in sorted(parts)],
                                }
                            ]
                        ),
                    )

                    yield event
                    continue

                if (
                    "contentBlockStart" in event
                    and "toolUse" in event["contentBlockStart"]["start"]
                ):
                    tool_call_part: "ToolCallPart" = {
                        "type": "tool_call",
                        "name": event["contentBlockStart"]["start"]["toolUse"]["name"],
                    }
                    parts[event["contentBlockStart"]["contentBlockIndex"]] = (
                        tool_call_part
                    )

                    yield event
                    continue

                if (
                    "contentBlockStart" in event
                    and "toolResult" in event["contentBlockStart"]["start"]
                ):
                    tool_call_response_part: "ToolCallResponsePart" = {
                        "type": "tool_call_response",
                    }
                    parts[event["contentBlockStart"]["contentBlockIndex"]] = (
                        tool_call_response_part
                    )

                    yield event
                    continue

                if "contentBlockDelta" not in event:
                    yield event
                    continue

                index = event["contentBlockDelta"]["contentBlockIndex"]
                delta = event["contentBlockDelta"]["delta"]

                if "text" in delta and index not in parts:
                    parts[index] = {"type": "text", "content": delta["text"]}

                    yield event
                    continue

                if "text" in delta:
                    cast("TextPart", parts[index])["content"] += delta["text"]

                    yield event
                    continue

                if "toolUse" in delta and index not in parts:
                    parts[index] = {
                        "type": "tool_call",
                        "arguments": delta["toolUse"]["input"],
                    }

                    yield event
                    continue

                if "toolUse" in delta and "arguments" in parts[index]:
                    cast("ToolCallPart", parts[index])["arguments"] += delta["toolUse"][
                        "input"
                    ]

                    yield event
                    continue

                if "toolUse" in delta:
                    cast("ToolCallPart", parts[index])["arguments"] = delta["toolUse"][
                        "input"
                    ]

                    yield event
                    continue

                if "toolResult" in delta and index not in parts:
                    parts[index] = {
                        "type": "tool_call_response",
                        "response": [
                            item
                            for item in delta["toolResult"]
                            if "json" in item or "text" in item
                        ],
                    }

                    yield event
                    continue

                if "toolResult" in delta and "response" in parts[index]:
                    cast("ToolCallResponsePart", parts[index])["response"] += [
                        item
                        for item in delta["toolResult"]
                        if "json" in item or "text" in item
                    ]

                    yield event
                    continue

                if "toolResult" in delta:
                    cast("ToolCallResponsePart", parts[index])["response"] = [
                        item
                        for item in delta["toolResult"]
                        if "json" in item or "text" in item
                    ]

                    yield event
                    continue

                if "reasoningContent" in delta and index not in parts:
                    parts[index] = {"type": "reasoning"}

                    if "text" in delta["reasoningContent"]:
                        cast("ReasoningPart", parts[index])["content"] = delta[
                            "reasoningContent"
                        ]["text"]

                    yield event
                    continue

                if (
                    "reasoningContent" in delta
                    and "text" in delta["reasoningContent"]
                    and "content" in parts[index]
                ):
                    cast("ReasoningPart", parts[index])["content"] += delta[
                        "reasoningContent"
                    ]["text"]

                    yield event
                    continue

                if "reasoningContent" in delta and "text" in delta["reasoningContent"]:
                    cast("ReasoningPart", parts[index])["content"] = delta[
                        "reasoningContent"
                    ]["text"]

                    yield event
                    continue

                if (
                    "image" in delta
                    and "source" in delta["image"]
                    and "s3Location" in delta["image"]["source"]
                ):
                    parts[index] = {
                        "type": "uri",
                        "modality": "image",
                        "uri": delta["image"]["source"]["s3Location"]["uri"],
                    }

                    yield event
                    continue

                if (
                    "image" in delta
                    and "source" in delta["image"]
                    and "bytes" in delta["image"]["source"]
                ):
                    parts[index] = {
                        "type": "blob",
                        "modality": "image",
                    }

                    yield event
                    continue

                yield event
        except GeneratorExit:
            raise  # Leave span open since underlying stream is not necessarily closed.
        except BaseException:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _finish_span(self, *exc_info)
            reraise(*exc_info)

        _finish_span(self, None, None, None)

    def sentry_patched_close(self: "EventStream[Any]") -> None:
        """
        Closes the AI Client Span unless `sentry_patched_iter()` closes the span first.
        """
        if not hasattr(self, "_span"):
            return original_close(self)

        try:
            original_close(self)
        except BaseException:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _finish_span(self, *exc_info)
            reraise(*exc_info)

        _finish_span(self, None, None, None)

    EventStream.__iter__ = sentry_patched_iter  # type: ignore[method-assign]
    EventStream.close = sentry_patched_close  # type: ignore[method-assign]


def _transform_system_instructions(
    system_instructions: "Union[list[dict[str, Any]], tuple[dict[str, Any], ...]]",
) -> "list[TextPart]":
    instruction_text_parts: "list[TextPart]" = []
    for block in system_instructions:
        if not isinstance(block, dict) or "text" not in block:
            continue

        instruction_text_parts.append({"type": "text", "content": block["text"]})

    return instruction_text_parts


def _transform_messages(
    input_messages: "Union[list[Mapping[str, Any]], tuple[Mapping[str, Any], ...]]",
) -> "list[InputMessage]":
    """
    Unlike other providers, AWS declares that input and output messages use the same message type.
    For this reason, the same transform is used for user and assistant messages.
    """
    messages: "list[InputMessage]" = []
    for message in input_messages:
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        if not isinstance(content, (list, tuple)) or "role" not in message:
            continue

        parts: "list[Union[TextPart, ReasoningPart, ToolCallPart, ToolCallResponsePart, UriPart, BlobPart]]" = []
        for block in content:
            if not isinstance(block, dict):
                continue

            if "text" in block:
                parts.append({"type": "text", "content": block["text"]})
                continue

            tool_use = block.get("toolUse")
            if tool_use is not None:
                tool_call_part: "ToolCallPart" = {"type": "tool_call"}

                if not isinstance(tool_use, dict):
                    parts.append(tool_call_part)
                    continue

                if "name" in tool_use:
                    tool_call_part["name"] = tool_use["name"]
                if "input" in tool_use:
                    tool_call_part["arguments"] = tool_use["input"]
                parts.append(tool_call_part)
                continue

            tool_result = block.get("toolResult")
            if tool_result is not None:
                tool_call_response_part: "ToolCallResponsePart" = {
                    "type": "tool_call_response",
                }

                if (
                    not isinstance(tool_result, dict)
                    or "content" not in tool_result
                    or not isinstance(tool_result["content"], (list, tuple))
                ):
                    parts.append(tool_call_response_part)
                    continue

                tool_call_response_part["response"] = [
                    item
                    for item in tool_result["content"]
                    if isinstance(item, dict) and ("json" in item or "text" in item)
                ]
                parts.append(tool_call_response_part)
                continue

            reasoning_content = block.get("reasoningContent")
            if reasoning_content is not None:
                reasoning_part: "ReasoningPart" = {"type": "reasoning"}

                if (
                    not isinstance(reasoning_content, dict)
                    or "reasoningText" not in reasoning_content
                    or not isinstance(reasoning_content["reasoningText"], dict)
                    or "text" not in reasoning_content["reasoningText"]
                ):
                    parts.append(reasoning_part)
                    continue

                reasoning_part["content"] = reasoning_content["reasoningText"]["text"]
                parts.append(reasoning_part)
                continue

            image = block.get("image")
            if (
                isinstance(image, dict)
                and isinstance(image.get("source"), dict)
                and "s3Location" in image["source"]
            ):
                uri_part: "UriPart" = {
                    "type": "uri",
                    "modality": "image",
                }
                s3_location = image["source"]["s3Location"]
                if not isinstance(s3_location, dict) or "uri" not in s3_location:
                    parts.append(uri_part)
                    continue

                uri_part["uri"] = s3_location["uri"]
                parts.append(uri_part)
                continue
            elif (
                isinstance(image, dict)
                and isinstance(image.get("source"), dict)
                and "bytes" in image["source"]
            ):
                parts.append({"type": "blob", "modality": "image"})
                continue

            video = block.get("video")
            if (
                isinstance(video, dict)
                and isinstance(video.get("source"), dict)
                and "s3Location" in video["source"]
            ):
                uri_part = {
                    "type": "uri",
                    "modality": "video",
                }
                s3_location = video["source"]["s3Location"]
                if not isinstance(s3_location, dict) or "uri" not in s3_location:
                    parts.append(uri_part)
                    continue

                uri_part["uri"] = s3_location["uri"]
                parts.append(uri_part)
                continue
            elif (
                isinstance(video, dict)
                and isinstance(video.get("source"), dict)
                and "bytes" in video["source"]
            ):
                parts.append({"type": "blob", "modality": "video"})
                continue

            audio = block.get("audio")
            if (
                isinstance(audio, dict)
                and isinstance(audio.get("source"), dict)
                and "s3Location" in audio["source"]
            ):
                uri_part = {
                    "type": "uri",
                    "modality": "audio",
                }
                s3_location = audio["source"]["s3Location"]
                if not isinstance(s3_location, dict) or "uri" not in s3_location:
                    parts.append(uri_part)
                    continue

                uri_part["uri"] = s3_location["uri"]
                parts.append(uri_part)
                continue
            elif (
                isinstance(audio, dict)
                and isinstance(audio.get("source"), dict)
                and "bytes" in audio["source"]
            ):
                parts.append({"type": "blob", "modality": "audio"})
                continue

            document = block.get("document")
            if (
                not isinstance(document, dict)
                or "source" not in document
                or not isinstance(document["source"], dict)
            ):
                continue

            if "s3Location" in document["source"]:
                uri_part = {
                    "type": "uri",
                    "modality": "document",
                }
                s3_location = document["source"]["s3Location"]
                if not isinstance(s3_location, dict) or "uri" not in s3_location:
                    parts.append(uri_part)
                    continue

                uri_part["uri"] = s3_location["uri"]
                parts.append(uri_part)
                continue

            if "bytes" in document["source"]:
                parts.append({"type": "blob", "modality": "document"})
                continue

        messages.append({"role": message["role"], "parts": parts})
    return messages


def _transform_tool_definitions(
    tools: "list[dict[str, Any]]",
) -> "list[ToolDefinition]":
    """
    Transform tool definitions to the schema used by the "gen_ai.tool.definitions" attribute.
    """
    result: "list[ToolDefinition]" = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue

        if "toolSpec" in tool:
            tool_definition: "ToolDefinition" = {"type": "function"}
            tool_spec = tool["toolSpec"]
            if not isinstance(tool_spec, dict):
                result.append(tool_definition)
                continue

            if "name" in tool_spec:
                tool_definition["name"] = tool_spec["name"]
            if "description" in tool_spec:
                tool_definition["description"] = tool_spec["description"]

            input_schema = tool_spec.get("inputSchema")
            if isinstance(input_schema, dict) and "json" in input_schema:
                tool_definition["parameters"] = input_schema["json"]

            result.append(tool_definition)
            continue

        if "systemTool" not in tool:
            continue

        system_tool = tool["systemTool"]
        if not isinstance(system_tool, dict) or "name" not in system_tool:
            continue

        result.append({"type": system_tool["name"]})

    return result


class _BedrockExtension(_ServiceExtension):
    __slots__ = ()
    skip_if_no_active_span = False

    def get_span_op(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return None
        return OP.GEN_AI_CHAT

    def get_span_origin(self, ctx: "AwsCallContext") -> "Optional[str]":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return None
        return "auto.ai.boto3"

    def get_request_attributes(self, ctx: "AwsCallContext") -> "Attributes":
        if ctx.operation_name != "Converse" and ctx.operation_name != "ConverseStream":
            return {}

        attributes: "Attributes" = {
            SPANDATA.GEN_AI_PROVIDER_NAME: "aws.bedrock",
            SPANDATA.GEN_AI_OPERATION_NAME: "chat",
            SPANDATA.GEN_AI_RESPONSE_STREAMING: ctx.operation_name == "ConverseStream",
        }

        model_id = ctx.params.get("modelId")
        if model_id is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_MODEL] = model_id

        inference_config = ctx.params.get("inferenceConfig")

        if isinstance(inference_config, dict):
            max_tokens = inference_config.get("maxTokens")
            if max_tokens is not None:
                attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens

            temperature = inference_config.get("temperature")
            if temperature is not None:
                attributes[SPANDATA.GEN_AI_REQUEST_TEMPERATURE] = temperature

            top_p = inference_config.get("topP")
            if top_p is not None:
                attributes[SPANDATA.GEN_AI_REQUEST_TOP_P] = top_p

        client_options = sentry_sdk.get_client().options
        if has_data_collection_enabled(client_options):
            if not client_options["data_collection"]["gen_ai"]["inputs"]:
                return attributes

        system_instructions = ctx.params.get("system")
        if (
            has_data_collection_enabled(client_options) or should_send_default_pii()
        ) and isinstance(system_instructions, (list, tuple)):
            attributes[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS] = json.dumps(
                _transform_system_instructions(system_instructions)
            )

        input_messages = ctx.params.get("messages")
        if (
            has_data_collection_enabled(client_options) or should_send_default_pii()
        ) and isinstance(input_messages, (list, tuple)):
            attributes[SPANDATA.GEN_AI_INPUT_MESSAGES] = json.dumps(
                _transform_messages(input_messages)
            )

        tool_config = ctx.params.get("toolConfig")
        if not isinstance(tool_config, dict):
            return attributes

        tools = tool_config.get("tools")
        if not isinstance(tools, list):
            return attributes

        attributes[SPANDATA.GEN_AI_TOOL_DEFINITIONS] = json.dumps(
            _transform_tool_definitions(tools)
        )

        return attributes

    def on_success_response(
        self,
        ctx: "AwsCallContext",
        response: "Any",
        span: "Union[Span, StreamedSpan]",
    ) -> None:
        set_on_span = (
            span.set_attribute if isinstance(span, StreamedSpan) else span.set_data
        )

        if ctx.operation_name == "Converse":
            response = cast("ConverseResponseTypeDef", response)
            usage = response["usage"]

            set_on_span(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"])
            set_on_span(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

            input_tokens = usage["inputTokens"]

            cache_read_tokens = usage.get("cacheReadInputTokens")
            if cache_read_tokens is not None:
                input_tokens += cache_read_tokens
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                )

            cache_write_tokens = usage.get("cacheWriteInputTokens")
            if cache_write_tokens is not None:
                input_tokens += cache_write_tokens
                set_on_span(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE, cache_write_tokens
                )

            set_on_span(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)

            finish_reasons: "list[str]" = [response["stopReason"]]
            set_on_span(SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reasons)

            client = sentry_sdk.get_client()
            if "message" in response["output"] and (
                (
                    has_data_collection_enabled(client.options)
                    and client.options["data_collection"]["gen_ai"]["outputs"]
                )
                or (
                    not has_data_collection_enabled(client.options)
                    and should_send_default_pii()
                )
            ):
                set_on_span(
                    SPANDATA.GEN_AI_OUTPUT_MESSAGES,
                    json.dumps(_transform_messages([response["output"]["message"]])),
                )

            if isinstance(span, StreamedSpan):
                span.end()
                return

            span.finish()
            return

        if ctx.operation_name == "ConverseStream":
            stream = cast("ConverseStreamResponseTypeDef", response)["stream"]
            stream._span = span  # type: ignore[attr-defined]
            return

        if isinstance(span, StreamedSpan):
            span.end()
            return

        span.finish()
