from typing import TYPE_CHECKING, cast

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

if TYPE_CHECKING:
    from typing import Any, Optional

    from mypy_boto3_bedrock_runtime.type_defs import ConverseResponseTypeDef

    from sentry_sdk._types import Attributes
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

        return {
            SPANDATA.GEN_AI_PROVIDER_NAME: "aws.bedrock",
            SPANDATA.GEN_AI_OPERATION_NAME: "chat",
            SPANDATA.GEN_AI_RESPONSE_STREAMING: ctx.operation_name == "ConverseStream",
        }

    def get_response_attributes(
        self,
        ctx: "AwsCallContext",
        response: "Any",
    ) -> "Attributes":
        if ctx.operation_name != "Converse" or "Error" in response:
            return {}

        usage = cast("ConverseResponseTypeDef", response)["usage"]
        attributes: "Attributes" = {
            SPANDATA.GEN_AI_USAGE_INPUT_TOKENS: usage["inputTokens"],
            SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS: usage["outputTokens"],
            SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS: usage["totalTokens"],
        }

        cache_read_tokens = usage.get("cacheReadInputTokens")
        if cache_read_tokens is not None:
            attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED] = cache_read_tokens

        cache_write_tokens = usage.get("cacheWriteInputTokens")
        if cache_write_tokens is not None:
            attributes[SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHE_WRITE] = (
                cache_write_tokens
            )

        return attributes
