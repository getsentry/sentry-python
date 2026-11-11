import json
import sys
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.traces import SpanStatus, StreamedSpan
from sentry_sdk.utils import (
    capture_internal_exceptions,
    reraise,
    should_be_treated_as_error,
)

try:
    from botocore.eventstream import EventStream
except ImportError:
    raise DidNotEnable("botocore is not installed")


if TYPE_CHECKING:
    from typing import Any, Iterator, Optional

    from mypy_boto3_bedrock_runtime.type_defs import (
        ConverseResponseTypeDef,
        ConverseStreamResponseTypeDef,
    )

    from sentry_sdk._types import Attributes, ToolDefinition
    from sentry_sdk.integrations.boto3._context import AwsCallContext


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
        span.status = SpanStatus.ERROR

    span.end()


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

        try:
            for event in original_iter(self):
                if "metadata" not in event:
                    yield event
                    continue

                usage = event["metadata"]["usage"]

                input_tokens = usage["inputTokens"]
                span.set_attribute(
                    SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"]
                )
                span.set_attribute(
                    SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"]
                )

                cache_read_tokens = usage.get("cacheReadInputTokens")
                if cache_read_tokens is not None:
                    input_tokens += cache_read_tokens
                    span.set_attribute(
                        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                    )

                cache_write_tokens = usage.get("cacheWriteInputTokens")
                if cache_write_tokens is not None:
                    input_tokens += cache_write_tokens
                    span.set_attribute(
                        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE,
                        cache_write_tokens,
                    )

                span.set_attribute(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
                    input_tokens,
                )

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
        if not client_options["data_collection"]["gen_ai"]["inputs"]:
            return attributes

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
        span: "StreamedSpan",
    ) -> None:
        if ctx.operation_name == "Converse":
            usage = cast("ConverseResponseTypeDef", response)["usage"]

            span.set_attribute(
                SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage["outputTokens"]
            )
            span.set_attribute(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage["totalTokens"])

            input_tokens = usage["inputTokens"]

            cache_read_tokens = usage.get("cacheReadInputTokens")
            if cache_read_tokens is not None:
                input_tokens += cache_read_tokens
                span.set_attribute(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED, cache_read_tokens
                )

            cache_write_tokens = usage.get("cacheWriteInputTokens")
            if cache_write_tokens is not None:
                input_tokens += cache_write_tokens
                span.set_attribute(
                    SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE, cache_write_tokens
                )

            span.set_attribute(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
            span.end()
            return

        if ctx.operation_name == "ConverseStream":
            stream = cast("ConverseStreamResponseTypeDef", response)["stream"]
            stream._span = span  # type: ignore[attr-defined]
            return

        span.end()
