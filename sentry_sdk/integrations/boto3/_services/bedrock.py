import json
from typing import TYPE_CHECKING

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

if TYPE_CHECKING:
    from typing import Any, Optional

    from sentry_sdk._types import Attributes, ToolDefinition
    from sentry_sdk.integrations.boto3._context import AwsCallContext


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
            if "inputSchema" in tool_spec and "json" in tool_spec["inputSchema"]:
                tool_definition["parameters"] = tool_spec["inputSchema"]["json"]

            result.append(tool_definition)
            continue

        if "systemTool" not in tool:
            continue

        system_tool = tool["systemTool"]
        if not isinstance(system_tool, dict) or "name" not in system_tool:
            continue

        result.append({"type": system_tool["name"]})

    return result
