import json
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.utils import has_data_collection_enabled

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes, ToolDefinition
    from sentry_sdk.integrations.boto3._context import AwsCallContext


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

    def get_response_attributes(
        self,
        ctx: "AwsCallContext",
        response: "Any",
    ) -> "Attributes":
        return {}
