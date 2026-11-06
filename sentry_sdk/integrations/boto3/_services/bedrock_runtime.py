import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.ai.utils import (
    MAX_SINGLE_MESSAGE_CONTENT_CHARS,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.boto3._instrumentation import (
    _finish_span,
    _get_error_attributes,
    _patch_streaming_body,
    _set_span_attributes,
)
from sentry_sdk.integrations.boto3._services.base import _ServiceExtension
from sentry_sdk.integrations.boto3.consts import AI_ORIGIN, IDENTIFIER
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import NoOpStreamedSpan
from sentry_sdk.tracing_utils import should_truncate_gen_ai_input
from sentry_sdk.utils import capture_internal_exceptions, has_data_collection_enabled

if TYPE_CHECKING:
    from typing import Any, Callable, Iterable, Literal, Optional, Union

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

try:
    from botocore.response import StreamingBody
except ImportError:
    raise DidNotEnable("botocore not installed")


# `gen_ai.provider.name` of Amazon Bedrock.
# https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/#gen-ai-provider-name
PROVIDER_NAME = "aws.bedrock"

# `gen_ai.operation.name` and span op of each instrumented operation.
GEN_AI_OPERATIONS = {
    "Converse": ("chat", OP.GEN_AI_CHAT),
    "InvokeModel": ("generate_content", OP.GEN_AI_GENERATE_CONTENT),
}

# `InvokeModel` request and response bodies have a model-specific format. For each
# model family: the object that holds the inference parameters in the request body,
# or `None` for the top level, and the attribute of each parameter.
# The model family is matched in the model ID, which can also be an inference profile
# ID or an ARN, e.g. `us.anthropic.claude-3-5-sonnet-20240620-v1:0`, in this order;
# `cohere.command-r` has to be matched before `cohere.command`.
# https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters.html
INVOKE_MODEL_PARAMETERS = {
    "amazon.titan": (
        "textGenerationConfig",
        {
            "maxTokenCount": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "topP": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stopSequences": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
    "amazon.nova": (
        "inferenceConfig",
        {
            "max_new_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stopSequences": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
    "anthropic.claude": (
        None,
        {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stop_sequences": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
    "meta.llama": (
        None,
        {
            "max_gen_len": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
        },
    ),
    "cohere.command-r": (
        None,
        {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stop_sequences": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
    "cohere.command": (
        None,
        {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stop_sequences": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
    "mistral": (
        None,
        {
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "stop": SPANDATA.GEN_AI_REQUEST_STOP_SEQUENCES,
        },
    ),
}

# the prompt of model families that take a single text prompt in `InvokeModel`.
INVOKE_MODEL_PROMPTS = {
    "amazon.titan": "inputText",
    "meta.llama": "prompt",
    "cohere.command-r": "message",
    "cohere.command": "prompt",
    "mistral": "prompt",
}

# larger `InvokeModel` request and response bodies aren't parsed, e.g. requests with
# images or image generation results.
MAX_INVOKE_MODEL_BODY_BYTES = 1_000_000


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

        if ctx.operation_name != "InvokeModel":
            with capture_internal_exceptions():
                attributes.update(_get_converse_request_attributes(ctx.params))

        return attributes

    def get_span_start_attributes(
        self, ctx: "AwsCallContext", span: "Union[Span, StreamedSpan]"
    ) -> "Attributes":
        # e.g. a span dropped by `ignore_spans`.
        if ctx.operation_name not in GEN_AI_OPERATIONS or isinstance(
            span, NoOpStreamedSpan
        ):
            return {}

        # the request body is parsed once, for its parameters and input messages.
        if ctx.operation_name == "InvokeModel":
            return _get_invoke_model_request_attributes(
                span,
                _get_model_family(ctx.params.get("modelId")),
                ctx.params.get("body"),
            )

        if not _should_record_gen_ai("inputs"):
            return {}

        return _get_input_messages_attributes(
            span,
            _transform_input_messages(
                ctx.params.get("messages"), _transform_content_blocks
            ),
        )

    def get_response_attributes(
        self, ctx: "AwsCallContext", response: "Any"
    ) -> "Attributes":
        if ctx.operation_name != "Converse":
            return {}

        return _get_converse_response_attributes(response)

    def instrument_response(
        self,
        ctx: "AwsCallContext",
        span: "Union[Span, StreamedSpan]",
        response: "Any",
    ) -> bool:
        """
        The `InvokeModel` response body is a `StreamingBody`. Its bytes are recorded
        as the caller reads them, and the span finishes once it's read or closed.
        """
        if ctx.operation_name != "InvokeModel" or isinstance(span, NoOpStreamedSpan):
            return False

        body = response.get("body") if isinstance(response, Mapping) else None
        if not isinstance(body, StreamingBody):
            return False

        model_family = _get_model_family(ctx.params.get("modelId"))
        headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
        chunks: "list[bytes]" = []
        size = 0

        def on_read(data: bytes) -> None:
            nonlocal size
            if model_family is None or size > MAX_INVOKE_MODEL_BODY_BYTES:
                return

            size += len(data)
            if size > MAX_INVOKE_MODEL_BODY_BYTES:
                chunks.clear()
            else:
                chunks.append(data)

        def on_finish(error: "Optional[BaseException]") -> None:
            attributes: "Attributes" = {}
            if error is not None:
                with capture_internal_exceptions():
                    attributes.update(_get_error_attributes(error))
            else:
                with capture_internal_exceptions():
                    attributes.update(
                        _get_invoke_model_response_attributes(
                            model_family, b"".join(chunks), headers
                        )
                    )

            with capture_internal_exceptions():
                _set_span_attributes(span, attributes)
            _finish_span(span, error)

        _patch_streaming_body(body, on_finish, on_read)
        return True


def _get_model_family(model_id: "Any") -> "Optional[str]":
    if not isinstance(model_id, str):
        return None

    for model_family in INVOKE_MODEL_PARAMETERS:
        if model_family in model_id:
            return model_family

    return None


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


def _get_converse_request_attributes(params: "Mapping[str, Any]") -> "Attributes":
    """
    The input messages are added by `get_span_start_attributes()`.
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

    if _should_record_gen_ai("inputs"):
        attributes.update(_get_converse_input_attributes(params))

    return attributes


def _get_converse_input_attributes(request: "Mapping[str, Any]") -> "Attributes":
    """
    Get the input attributes, except the input messages, of a request in the Converse
    format, which `InvokeModel` requests to Amazon Nova models also use.
    """
    tool_config = request.get("toolConfig")
    return _get_input_attributes(
        _transform_system_instructions(request.get("system")),
        _transform_tool_definitions(
            tool_config.get("tools") if isinstance(tool_config, Mapping) else None
        ),
    )


def _get_invoke_model_request_attributes(
    span: "Union[Span, StreamedSpan]", model_family: "Optional[str]", body: "Any"
) -> "Attributes":
    """
    A request body that is a file-like object isn't read, as that would consume it.
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters.html
    """
    if (
        model_family is None
        or not isinstance(body, (bytes, bytearray, str))
        or len(body) > MAX_INVOKE_MODEL_BODY_BYTES
    ):
        return {}

    try:
        request = json.loads(body)
    except ValueError:
        # the request fails, as Bedrock rejects it.
        return {}

    if not isinstance(request, Mapping):
        return {}

    attributes: "Attributes" = {}

    parameters_key, parameters = INVOKE_MODEL_PARAMETERS[model_family]
    request_parameters = (
        request.get(parameters_key) if parameters_key is not None else request
    )
    if isinstance(request_parameters, Mapping):
        for key, attribute in parameters.items():
            value = request_parameters.get(key)
            if isinstance(value, (int, float, str, list)):
                attributes[attribute] = value

    if not _should_record_gen_ai("inputs"):
        return attributes

    input_messages: "list[InputMessage]" = []
    if model_family == "amazon.nova":
        attributes.update(_get_converse_input_attributes(request))
        input_messages = _transform_input_messages(
            request.get("messages"), _transform_content_blocks
        )
    elif model_family == "anthropic.claude":
        attributes.update(
            _get_input_attributes(
                _transform_system_instructions(request.get("system")),
                _transform_anthropic_tool_definitions(request.get("tools")),
            )
        )
        input_messages = _transform_input_messages(
            request.get("messages"), _transform_anthropic_content
        )
    else:
        # Cohere Command R's system prompt.
        attributes.update(
            _get_input_attributes(
                _transform_system_instructions(request.get("preamble")), []
            )
        )
        prompt = request.get(INVOKE_MODEL_PROMPTS[model_family])
        if isinstance(prompt, str):
            input_messages = [
                {"role": "user", "parts": [{"type": "text", "content": prompt}]}
            ]

    with capture_internal_exceptions():
        attributes.update(_get_input_messages_attributes(span, input_messages))

    return attributes


def _get_input_attributes(
    system_instructions: "list[TextPart]", tool_definitions: "list[ToolDefinition]"
) -> "Attributes":
    attributes: "Attributes" = {}

    # serialize each attribute separately so that an unserializable value only drops
    # the attribute it's in.
    with capture_internal_exceptions():
        if system_instructions:
            attributes[SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS] = json.dumps(
                system_instructions
            )

    with capture_internal_exceptions():
        if tool_definitions:
            attributes[SPANDATA.GEN_AI_TOOL_DEFINITIONS] = json.dumps(tool_definitions)

    return attributes


def _get_input_messages_attributes(
    span: "Union[Span, StreamedSpan]", input_messages: "list[InputMessage]"
) -> "Attributes":
    """
    Like the other `gen_ai` integrations, only the last message is kept when
    `gen_ai` spans are sent in transactions, and its text, tool call arguments,
    and tool results share one truncation limit, so that long histories, prompts,
    and tool results don't exceed the transaction size limit. JSON arguments and
    results count with their serialized length, and are replaced by their
    truncated serialization when they don't fit.
    """
    messages: "Optional[list[dict[str, Any]]]" = cast(
        "list[dict[str, Any]]", input_messages
    )
    if should_truncate_gen_ai_input(sentry_sdk.get_client().options):
        messages = truncate_and_annotate_messages(
            messages, span, sentry_sdk.get_current_scope()
        )
        if messages:
            # `truncate_and_annotate_messages()` only truncates a message's
            # `content`, but this format keeps the text, tool call arguments, and
            # tool results in each part. They share the message's limit.
            remaining = MAX_SINGLE_MESSAGE_CONTENT_CHARS

            def truncate(value: "Any") -> "Any":
                nonlocal remaining
                text = value if isinstance(value, str) else json.dumps(value)
                if len(text) > remaining:
                    value = text[:remaining] + "..."
                remaining = max(remaining - len(text), 0)
                return value

            # the last message is a copy, so its parts can be replaced.
            for part in messages[-1].get("parts") or []:
                for key in ("content", "arguments"):
                    if key in part:
                        part[key] = truncate(part[key])
                result = part.get("result")
                if isinstance(result, list):
                    part["result"] = [truncate(item) for item in result]
                elif result is not None:
                    part["result"] = truncate(result)

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
        attributes.update(
            _get_usage_attributes(
                usage.get("inputTokens"),
                usage.get("outputTokens"),
                usage.get("cacheReadInputTokens"),
                usage.get("cacheWriteInputTokens"),
                usage.get("totalTokens"),
            )
        )

    output = response.get("output")
    message = output.get("message") if isinstance(output, Mapping) else None
    if isinstance(message, Mapping):
        attributes.update(
            _get_output_attributes(
                [_to_output_message(_transform_content_blocks(message.get("content")))]
            )
        )

    return attributes


def _get_invoke_model_response_attributes(
    model_family: "Optional[str]", body: bytes, headers: "Mapping[str, str]"
) -> "Attributes":
    """
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters.html
    """
    attributes: "Attributes" = {}

    response = None
    if model_family is not None and body:
        try:
            response = json.loads(body)
        except ValueError:
            # e.g. a body that was closed before being read completely.
            pass

    if isinstance(response, Mapping):
        with capture_internal_exceptions():
            attributes.update(_parse_invoke_model_response(model_family, response))

    # Bedrock reports the token usage of every model in response headers.
    for attribute, header in (
        (SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, "x-amzn-bedrock-input-token-count"),
        (SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, "x-amzn-bedrock-output-token-count"),
    ):
        if attribute not in attributes and header in headers:
            with capture_internal_exceptions():
                attributes[attribute] = int(headers[header])

    return attributes


def _parse_invoke_model_response(
    model_family: "Optional[str]", response: "Mapping[str, Any]"
) -> "Attributes":
    attributes: "Attributes" = {}
    finish_reasons: "list[Any]" = []
    output_messages: "list[OutputMessage]" = []

    if model_family == "amazon.nova":
        usage = response.get("usage") or {}
        attributes.update(
            _get_usage_attributes(
                usage.get("inputTokens"),
                usage.get("outputTokens"),
                usage.get("cacheReadInputTokenCount"),
                usage.get("cacheWriteInputTokenCount"),
                usage.get("totalTokens"),
            )
        )
        finish_reasons = [response.get("stopReason")]
        message = (response.get("output") or {}).get("message")
        if isinstance(message, Mapping):
            output_messages = [
                _to_output_message(_transform_content_blocks(message.get("content")))
            ]

    elif model_family == "anthropic.claude":
        usage = response.get("usage") or {}
        attributes.update(
            _get_usage_attributes(
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"),
                usage.get("cache_creation_input_tokens"),
            )
        )
        finish_reasons = [response.get("stop_reason")]
        output_messages = [
            _to_output_message(_transform_anthropic_content(response.get("content")))
        ]

    elif model_family == "amazon.titan":
        results = response.get("results") or []
        attributes.update(
            _get_usage_attributes(
                response.get("inputTextTokenCount"),
                results[0].get("tokenCount") if results else None,
            )
        )
        finish_reasons = [result.get("completionReason") for result in results]
        output_messages = _to_text_output_messages(
            result.get("outputText") for result in results
        )

    elif model_family == "meta.llama":
        attributes.update(
            _get_usage_attributes(
                response.get("prompt_token_count"),
                response.get("generation_token_count"),
            )
        )
        finish_reasons = [response.get("stop_reason")]
        output_messages = _to_text_output_messages([response.get("generation")])

    elif model_family == "cohere.command-r":
        finish_reasons = [response.get("finish_reason")]
        output_messages = _to_text_output_messages([response.get("text")])

    elif model_family == "cohere.command":
        generations = response.get("generations") or []
        finish_reasons = [generation.get("finish_reason") for generation in generations]
        output_messages = _to_text_output_messages(
            generation.get("text") for generation in generations
        )

    elif model_family == "mistral":
        outputs = response.get("outputs") or []
        finish_reasons = [output.get("stop_reason") for output in outputs]
        output_messages = _to_text_output_messages(
            output.get("text") for output in outputs
        )

    finish_reasons = [reason for reason in finish_reasons if isinstance(reason, str)]
    if finish_reasons:
        attributes[SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS] = finish_reasons

    attributes.update(_get_output_attributes(output_messages))
    return attributes


def _get_usage_attributes(
    input_tokens: "Any",
    output_tokens: "Any",
    cache_read_input_tokens: "Any" = None,
    cache_write_input_tokens: "Any" = None,
    total_tokens: "Any" = None,
) -> "Attributes":
    """
    Bedrock's input tokens exclude the tokens read from or written to the prompt
    cache, but `gen_ai.usage.input_tokens` includes them.
    https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html
    """
    attributes: "Attributes" = {}

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


def _get_output_attributes(output_messages: "list[OutputMessage]") -> "Attributes":
    attributes: "Attributes" = {}
    if not output_messages or not _should_record_gen_ai("outputs"):
        return attributes

    with capture_internal_exceptions():
        attributes[SPANDATA.GEN_AI_OUTPUT_MESSAGES] = json.dumps(output_messages)

    return attributes


def _to_output_message(parts: "list[ContentPart]") -> "OutputMessage":
    output_message: "OutputMessage" = {"role": "assistant", "parts": []}
    for part in parts:
        # models don't respond with tool results.
        if part["type"] != "tool_call_response":
            output_message["parts"].append(part)

    return output_message


def _to_text_output_messages(texts: "Iterable[Any]") -> "list[OutputMessage]":
    return [
        {"role": "assistant", "parts": [{"type": "text", "content": text}]}
        for text in texts
        if isinstance(text, str)
    ]


def _transform_system_instructions(system: "Any") -> "list[TextPart]":
    """
    Transform a system prompt, or a list of Converse or Anthropic system content blocks.
    """
    if isinstance(system, str):
        return [{"type": "text", "content": system}]

    if not isinstance(system, (list, tuple)):
        return []

    return [
        {"type": "text", "content": block["text"]}
        for block in system
        if isinstance(block, Mapping) and isinstance(block.get("text"), str)
    ]


def _transform_input_messages(
    messages: "Any", transform_content: "Callable[[Any], list[ContentPart]]"
) -> "list[InputMessage]":
    """
    Tool results are sent in `user` messages; they're recorded as `tool` messages,
    which is how `gen_ai.input.messages` represents them.
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
        for part in transform_content(message.get("content")):
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


def _transform_anthropic_content(content: "Any") -> "list[ContentPart]":
    """
    Transform the text, thinking, tool use, and tool result content of an Anthropic
    Claude message. Media content, e.g. images and documents, is not recorded.
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages-request-response.html
    """
    if isinstance(content, str):
        return [{"type": "text", "content": content}]

    if not isinstance(content, (list, tuple)):
        return []

    parts: "list[ContentPart]" = []
    for block in content:
        if not isinstance(block, Mapping):
            continue

        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            parts.append({"type": "text", "content": block["text"]})

        elif block_type == "thinking" and isinstance(block.get("thinking"), str):
            parts.append({"type": "reasoning", "content": block["thinking"]})

        elif block_type == "tool_use":
            tool_call: "ToolCallPart" = {"type": "tool_call"}
            if "id" in block:
                tool_call["id"] = block["id"]
            if "name" in block:
                tool_call["name"] = block["name"]
            if "input" in block:
                tool_call["arguments"] = block["input"]
            parts.append(tool_call)

        elif block_type == "tool_result":
            tool_response: "ToolCallResponsePart" = {"type": "tool_call_response"}
            if "tool_use_id" in block:
                tool_response["id"] = block["tool_use_id"]

            result = [
                part["content"]
                for part in _transform_anthropic_content(block.get("content"))
                if part["type"] == "text"
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

        input_schema = tool_spec.get("inputSchema")
        tool_definitions.append(
            _to_tool_definition(
                tool_spec,
                input_schema.get("json") if isinstance(input_schema, Mapping) else None,
            )
        )

    return tool_definitions


def _transform_anthropic_tool_definitions(tools: "Any") -> "list[ToolDefinition]":
    if not isinstance(tools, (list, tuple)):
        return []

    return [
        _to_tool_definition(tool, tool.get("input_schema"))
        for tool in tools
        if isinstance(tool, Mapping)
    ]


def _to_tool_definition(
    tool: "Mapping[str, Any]", parameters: "Any"
) -> "ToolDefinition":
    tool_definition: "ToolDefinition" = {"type": "function"}
    if "name" in tool:
        tool_definition["name"] = tool["name"]
    if "description" in tool:
        tool_definition["description"] = tool["description"]
    if isinstance(parameters, Mapping):
        tool_definition["parameters"] = dict(parameters)

    return tool_definition
