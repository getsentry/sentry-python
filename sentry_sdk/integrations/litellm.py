import copy
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk import consts
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import (
    get_start_span_function,
    normalize_message_roles,
    set_data_normalized,
    truncate_and_annotate_messages,
    transform_openai_content_part,
    truncate_and_annotate_embedding_inputs,
)
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import event_from_exception

if TYPE_CHECKING:
    from typing import Any, Dict, List
    from datetime import datetime

try:
    import litellm  # type: ignore[import-not-found]
    from litellm import input_callback, success_callback, failure_callback
    from litellm.types.llms.openai import (  # type: ignore[import-not-found]
        ResponseAPIUsage,
        ResponseCompletedEvent,
        ResponsesAPIResponse,
    )
except ImportError:
    raise DidNotEnable("LiteLLM not installed")


def _get_metadata_dict(kwargs: "Dict[str, Any]") -> "Dict[str, Any]":
    """Get the metadata dictionary from the kwargs."""
    litellm_params = kwargs.setdefault("litellm_params", {})

    # we need this weird little dance, as metadata might be set but may be None initially
    metadata = litellm_params.get("metadata")
    if metadata is None:
        metadata = {}
        litellm_params["metadata"] = metadata
    return metadata


def _convert_message_parts(messages: "List[Dict[str, Any]]") -> "List[Dict[str, Any]]":
    """
    Convert the message parts from OpenAI format to the `gen_ai.request.messages` format
    using the OpenAI-specific transformer (LiteLLM uses OpenAI's message format).

    Deep copies messages to avoid mutating original kwargs.
    """
    # Deep copy to avoid mutating original messages from kwargs
    messages = copy.deepcopy(messages)

    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, (list, tuple)):
            transformed = []
            for item in content:
                if isinstance(item, dict):
                    result = transform_openai_content_part(item)
                    # If transformation succeeded, use the result; otherwise keep original
                    transformed.append(result if result is not None else item)
                else:
                    transformed.append(item)
            message["content"] = transformed
    return messages


def _record_responses_conversation_id(
    span: "Any", complete_input: "Dict[str, Any]"
) -> None:
    """Set the conversation id on the span when the Responses API request carries one."""
    conversation = complete_input.get("conversation")
    if conversation is None:
        return

    if isinstance(conversation, str):
        conversation_id = conversation
    elif isinstance(conversation, dict):
        conversation_id = conversation.get("id")
    else:
        conversation_id = None

    if conversation_id is not None:
        set_data_normalized(span, SPANDATA.GEN_AI_CONVERSATION_ID, conversation_id)


def _record_responses_input_messages(
    span: "Any", scope: "Any", responses_input: "Any"
) -> None:
    """Record the request messages for a Responses API call."""
    if not responses_input:
        return

    # `input` is either a string or a list of message dicts (same shape as
    # the OpenAI Responses API).
    if isinstance(responses_input, str):
        input_messages = [responses_input]
    else:
        input_messages = list(responses_input)
    normalized = normalize_message_roles(input_messages)  # type: ignore[arg-type]
    messages_data = truncate_and_annotate_messages(normalized, span, scope)
    if messages_data is not None:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_MESSAGES,
            messages_data,
            unpack=False,
        )


def _input_callback(kwargs: "Dict[str, Any]") -> None:
    """Handle the start of a request."""
    integration = sentry_sdk.get_client().get_integration(LiteLLMIntegration)

    if integration is None:
        return

    # Get key parameters
    full_model = kwargs.get("model", "")
    try:
        model, provider, _, _ = litellm.get_llm_provider(full_model)
    except Exception:
        model = full_model
        provider = "unknown"

    call_type = kwargs.get("call_type", None)
    if call_type == "embedding" or call_type == "aembedding":
        operation = "embeddings"
        op = consts.OP.GEN_AI_EMBEDDINGS
    elif call_type == "responses" or call_type == "aresponses":
        operation = "responses"
        op = consts.OP.GEN_AI_RESPONSES
    else:
        operation = "chat"
        op = consts.OP.GEN_AI_CHAT

    # Start a new span/transaction
    span = get_start_span_function()(
        op=op,
        name=f"{operation} {model}",
        origin=LiteLLMIntegration.origin,
    )
    span.__enter__()

    # Store span for later
    _get_metadata_dict(kwargs)["_sentry_span"] = span

    # Set basic data
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, provider)
    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, operation)

    # Per-operation request data. Conversation id (responses) is set
    # unconditionally; user-content fields are gated on PII / include_prompts.
    record_prompts = should_send_default_pii() and integration.include_prompts
    scope = sentry_sdk.get_current_scope()

    if operation == "embeddings":
        if record_prompts:
            embedding_input = kwargs.get("input")
            if embedding_input:
                input_list = (
                    embedding_input
                    if isinstance(embedding_input, list)
                    else [embedding_input]
                )
                messages_data = truncate_and_annotate_embedding_inputs(
                    input_list, span, scope
                )
                if messages_data is not None:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_EMBEDDINGS_INPUT,
                        messages_data,
                        unpack=False,
                    )

    elif operation == "responses":
        # litellm unpacks `extra_body` into the request body, so the
        # `conversation` field shows up in additional_args.complete_input_dict
        # rather than as a top-level kwarg.
        complete_input = (kwargs.get("additional_args") or {}).get(
            "complete_input_dict"
        ) or {}
        _record_responses_conversation_id(span, complete_input)
        if record_prompts:
            _record_responses_input_messages(span, scope, kwargs.get("input"))

    else:
        # Chat completions.
        if record_prompts:
            messages = kwargs.get("messages", [])
            if messages:
                messages = _convert_message_parts(messages)
                messages_data = truncate_and_annotate_messages(messages, span, scope)
                if messages_data is not None:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        messages_data,
                        unpack=False,
                    )

    # Record other parameters
    params = {
        "model": SPANDATA.GEN_AI_REQUEST_MODEL,
        "stream": SPANDATA.GEN_AI_RESPONSE_STREAMING,
        "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
        "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
        "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
        "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
        "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
    }
    for key, attribute in params.items():
        value = kwargs.get(key)
        if value is not None:
            set_data_normalized(span, attribute, value)


async def _async_input_callback(kwargs: "Dict[str, Any]") -> None:
    return _input_callback(kwargs)


def _record_chat_response_messages(span: "Any", response: "Any") -> None:
    """Record response.text from a Chat Completions response."""
    response_messages = []
    for choice in response.choices:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        if hasattr(message, "model_dump"):
            response_messages.append(message.model_dump())
        elif hasattr(message, "dict"):
            response_messages.append(message.dict())
        else:
            # Fallback for basic message objects
            msg = {}
            if hasattr(message, "role"):
                msg["role"] = message.role
            if hasattr(message, "content"):
                msg["content"] = message.content
            if hasattr(message, "tool_calls"):
                msg["tool_calls"] = message.tool_calls
            response_messages.append(msg)

    if response_messages:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, response_messages)


def _record_responses_output(span: "Any", response: "ResponsesAPIResponse") -> None:
    """Record response text and tool calls from a Responses API response."""
    output_text = []  # type: List[Any]
    tool_calls = []  # type: List[Any]
    for output in response.output:
        output_type = getattr(output, "type", None)
        if output_type == "function_call":
            if hasattr(output, "model_dump"):
                tool_calls.append(output.model_dump())
            elif hasattr(output, "dict"):
                tool_calls.append(output.dict())
        elif output_type == "message":
            for content_item in getattr(output, "content", []) or []:
                text = getattr(content_item, "text", None)
                if text is not None:
                    output_text.append(text)
                elif hasattr(content_item, "model_dump"):
                    output_text.append(content_item.model_dump())
                elif hasattr(content_item, "dict"):
                    output_text.append(content_item.dict())

    if tool_calls:
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
            tool_calls,
            unpack=False,
        )
    if output_text:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, output_text)


def _record_token_usage_from_response(span: "Any", response: "Any") -> None:
    """Record token usage. The shape of ``usage`` depends on the litellm
    processing pipeline rather than the API path:

    - ``ResponseAPIUsage``: raw Responses API usage (``input_tokens`` /
      ``output_tokens``). Seen when litellm has not yet normalized the value.
    - ``dict``: chat-style dict (``prompt_tokens`` / ``completion_tokens``).
      litellm assembles streaming Responses API usage as a dict.
    - Otherwise: chat-style Pydantic ``Usage`` (``prompt_tokens`` /
      ``completion_tokens``). Used for Chat Completions, Embeddings, and
      non-streaming Responses API after litellm's post-processing.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    if isinstance(usage, ResponseAPIUsage):
        record_token_usage(
            span,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )
    elif isinstance(usage, dict):
        record_token_usage(
            span,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
    else:
        record_token_usage(
            span,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )


def _success_callback(
    kwargs: "Dict[str, Any]",
    response: "Any",
    start_time: "datetime",
    end_time: "datetime",
) -> None:
    """Handle a successful chat completion, embeddings, or Responses API call.

    The shape of `response` differs between API paths:
      - Chat Completions: ModelResponse with ``.choices[].message`` and
        ``.usage`` carrying ``prompt_tokens`` / ``completion_tokens``.
      - Responses API (non-streaming): ResponsesAPIResponse with ``.output[]``
        items (``message`` / ``function_call``) and ``.usage`` carrying
        ``input_tokens`` / ``output_tokens``.
      - Responses API (streaming): a ResponseCompletedEvent wrapping a
        ``ResponsesAPIResponse``, which we unwrap below.
      - Embeddings: CreateEmbeddingResponse with ``.usage`` only (no choices
        or output).
    """

    metadata = _get_metadata_dict(kwargs)
    span = metadata.get("_sentry_span")
    if span is None:
        return

    integration = sentry_sdk.get_client().get_integration(LiteLLMIntegration)
    if integration is None:
        return

    # Streaming Responses API: unwrap the ResponseCompletedEvent so the rest of
    # the function sees the assembled ResponsesAPIResponse directly.
    if isinstance(response, ResponseCompletedEvent):
        response = response.response

    try:
        # `model` is set by all API shapes (chat / responses / embeddings).
        if hasattr(response, "model"):
            set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

        if should_send_default_pii() and integration.include_prompts:
            if isinstance(response, ResponsesAPIResponse):
                _record_responses_output(span, response)
            elif hasattr(response, "choices"):
                _record_chat_response_messages(span, response)

        _record_token_usage_from_response(span, response)

    finally:
        is_streaming = kwargs.get("stream")
        # Callback is fired multiple times when streaming a response.
        # Streaming flag checked at https://github.com/BerriAI/litellm/blob/33c3f13443eaf990ac8c6e3da78bddbc2b7d0e7a/litellm/litellm_core_utils/litellm_logging.py#L1603
        if (
            is_streaming is not True
            or "complete_streaming_response" in kwargs
            or "async_complete_streaming_response" in kwargs
        ):
            span = metadata.pop("_sentry_span", None)
            if span is not None:
                span.__exit__(None, None, None)


async def _async_success_callback(
    kwargs: "Dict[str, Any]",
    completion_response: "Any",
    start_time: "datetime",
    end_time: "datetime",
) -> None:
    return _success_callback(
        kwargs,
        completion_response,
        start_time,
        end_time,
    )


def _failure_callback(
    kwargs: "Dict[str, Any]",
    exception: Exception,
    start_time: "datetime",
    end_time: "datetime",
) -> None:
    """Handle request failure."""
    span = _get_metadata_dict(kwargs).get("_sentry_span")
    if span is None:
        return

    try:
        # Capture the exception
        event, hint = event_from_exception(
            exception,
            client_options=sentry_sdk.get_client().options,
            mechanism={"type": "litellm", "handled": False},
        )
        sentry_sdk.capture_event(event, hint=hint)
    finally:
        # Always finish the span and clean up
        span.__exit__(type(exception), exception, None)


class LiteLLMIntegration(Integration):
    """
    LiteLLM integration for Sentry.

    This integration automatically captures LiteLLM API calls and sends them to Sentry
    for monitoring and error tracking. It supports all 100+ LLM providers that LiteLLM
    supports, including OpenAI, Anthropic, Google, Cohere, and many others.

    Features:
    - Automatic exception capture for all LiteLLM calls
    - Token usage tracking across all providers
    - Provider detection and attribution
    - Input/output message capture (configurable)
    - Streaming response support
    - Cost tracking integration

    Usage:

    ```python
    import litellm
    import sentry_sdk

    # Initialize Sentry with the LiteLLM integration
    sentry_sdk.init(
        dsn="your-dsn",
        send_default_pii=True
        integrations=[
            sentry_sdk.integrations.LiteLLMIntegration(
                include_prompts=True  # Set to False to exclude message content
            )
        ]
    )

    # All LiteLLM calls will now be monitored
    response = litellm.completion(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello!"}]
    )
    ```

    Configuration:
    - include_prompts (bool): Whether to include prompts and responses in spans.
      Defaults to True. Set to False to exclude potentially sensitive data.
    """

    identifier = "litellm"
    origin = f"auto.ai.{identifier}"

    def __init__(self: "LiteLLMIntegration", include_prompts: bool = True) -> None:
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        """Set up LiteLLM callbacks for monitoring."""
        litellm.input_callback = input_callback or []
        if _input_callback not in litellm.input_callback:
            litellm.input_callback.append(_input_callback)
        if _async_input_callback not in litellm.input_callback:
            litellm.input_callback.append(_async_input_callback)

        litellm.success_callback = success_callback or []
        if _success_callback not in litellm.success_callback:
            litellm.success_callback.append(_success_callback)
        if _async_success_callback not in litellm.success_callback:
            litellm.success_callback.append(_async_success_callback)

        litellm.failure_callback = failure_callback or []
        if _failure_callback not in litellm.failure_callback:
            litellm.failure_callback.append(_failure_callback)
