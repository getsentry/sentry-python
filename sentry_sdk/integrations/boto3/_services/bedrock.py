from typing import TYPE_CHECKING

from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension

if TYPE_CHECKING:
    from typing import Any, Optional

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

        attributes: "Attributes" = {
            SPANDATA.GEN_AI_PROVIDER_NAME: "aws.bedrock",
            SPANDATA.GEN_AI_OPERATION_NAME: "chat",
            SPANDATA.GEN_AI_RESPONSE_STREAMING: ctx.operation_name == "ConverseStream",
        }

        inference_config = ctx.params.get("inferenceConfig")

        if not isinstance(inference_config, dict):
            return attributes

        max_tokens = inference_config.get("maxTokens")
        if max_tokens is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens

        temperature = inference_config.get("temperature")
        if temperature is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_TEMPERATURE] = temperature

        top_p = inference_config.get("topP")
        if top_p is not None:
            attributes[SPANDATA.GEN_AI_REQUEST_TOP_P] = top_p

        return attributes

    def get_response_attributes(
        self,
        ctx: "AwsCallContext",
        response: "Any",
    ) -> "Attributes":
        return {}
