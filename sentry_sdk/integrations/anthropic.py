import sys
import json
from collections.abc import Iterable
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import (
    GEN_AI_ALLOWED_MESSAGE_ROLES,
    set_data_normalized,
    normalize_message_roles,
    truncate_and_annotate_messages,
    get_start_span_function,
    transform_anthropic_content_part,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import _check_minimum_version, DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    package_version,
    safe_serialize,
    reraise,
)

try:
    try:
        from anthropic import NotGiven
    except ImportError:
        NotGiven = None

    try:
        from anthropic import Omit
    except ImportError:
        Omit = None

    from anthropic import Stream, AsyncStream
    from anthropic.resources import AsyncMessages, Messages
    from anthropic.lib.streaming import (
        MessageStreamManager,
        MessageStream,
        AsyncMessageStreamManager,
        AsyncMessageStream,
    )

    from anthropic.types import (
        MessageStartEvent,
        MessageDeltaEvent,
        MessageStopEvent,
        ContentBlockStartEvent,
        ContentBlockDeltaEvent,
        ContentBlockStopEvent,
    )

    if TYPE_CHECKING:
        from anthropic.types import MessageStreamEvent, TextBlockParam
except ImportError:
    raise DidNotEnable("Anthropic not installed")

if TYPE_CHECKING:
    from typing import (
        Any,
        AsyncIterator,
        Iterator,
        Optional,
        Union,
        Callable,
        Awaitable,
    )
    from sentry_sdk.tracing import Span
    from sentry_sdk._types import TextPart

    from anthropic.types import (
        RawMessageStreamEvent,
        MessageParam,
        ModelParam,
        TextBlockParam,
        ToolUnionParam,
    )


class _RecordedUsage:
    output_tokens: int = 0
    input_tokens: int = 0
    cache_write_input_tokens: "Optional[int]" = 0
    cache_read_input_tokens: "Optional[int]" = 0


class _StreamSpanContext:
    """
    Sets accumulated data on the stream's span and finishes the span on exit.
    Is a no-op if the stream has no span set, i.e., when the span has already been finished.
    """

    def __init__(
        self,
        stream: "Union[Stream, MessageStream, AsyncStream, AsyncMessageStream]",
        # Flag to avoid unreachable branches when the stream state is known to be initialized (stream._model, etc. are set).
        guaranteed_streaming_state: bool = False,
    ) -> None:
        self._stream = stream
        self._guaranteed_streaming_state = guaranteed_streaming_state

    def __enter__(self) -> "_StreamSpanContext":
        return self

    def __exit__(
        self,
        exc_type: "Optional[type[BaseException]]",
        exc_val: "Optional[BaseException]",
        exc_tb: "Optional[Any]",
    ) -> None:
        with capture_internal_exceptions():
            if not hasattr(self._stream, "_span"):
                return

            if not self._guaranteed_streaming_state and not hasattr(
                self._stream, "_model"
            ):
                self._stream._span.__exit__(exc_type, exc_val, exc_tb)
                del self._stream._span
                return

            _set_streaming_output_data(
                span=self._stream._span,
                integration=self._stream._integration,
                model=self._stream._model,
                usage=self._stream._usage,
                content_blocks=self._stream._content_blocks,
                response_id=self._stream._response_id,
                finish_reason=self._stream._finish_reason,
            )

            self._stream._span.__exit__(exc_type, exc_val, exc_tb)
            del self._stream._span


class AnthropicIntegration(Integration):
    identifier = "anthropic"
    origin = f"auto.ai.{identifier}"

    def __init__(self: "AnthropicIntegration", include_prompts: bool = True) -> None:
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once() -> None:
        version = package_version("anthropic")
        _check_minimum_version(AnthropicIntegration, version)

        """
        client.messages.create(stream=True) can return an instance of the Stream class, which implements the iterator protocol.
        Analogously, the function can return an AsyncStream, which implements the asynchronous iterator protocol.
        The private _iterator variable and the close() method are patched. During iteration over the _iterator generator,
        information from intercepted events is accumulated and used to populate output attributes on the AI Client Span.

        The span can be finished in two places:
        - When the user exits the context manager or directly calls close(), the patched close() finishes the span.
        - When iteration ends, the finally block in the _iterator wrapper finishes the span.

        Both paths may run. For example, the context manager exit can follow iterator exhaustion.
        """
        Messages.create = _wrap_message_create(Messages.create)
        Stream.close = _wrap_close(Stream.close)

        AsyncMessages.create = _wrap_message_create_async(AsyncMessages.create)
        AsyncStream.close = _wrap_async_close(AsyncStream.close)

        """
        client.messages.stream() patches are analogous to the patches for client.messages.create(stream=True) described above.
        """
        Messages.stream = _wrap_message_stream(Messages.stream)
        MessageStreamManager.__enter__ = _wrap_message_stream_manager_enter(
            MessageStreamManager.__enter__
        )

        # Before https://github.com/anthropics/anthropic-sdk-python/commit/b1a1c0354a9aca450a7d512fdbdeb59c0ead688a
        # MessageStream inherits from Stream, so patching Stream is sufficient on these versions.
        if not issubclass(MessageStream, Stream):
            MessageStream.close = _wrap_close(MessageStream.close)

        AsyncMessages.stream = _wrap_async_message_stream(AsyncMessages.stream)
        AsyncMessageStreamManager.__aenter__ = (
            _wrap_async_message_stream_manager_aenter(
                AsyncMessageStreamManager.__aenter__
            )
        )

        # Before https://github.com/anthropics/anthropic-sdk-python/commit/b1a1c0354a9aca450a7d512fdbdeb59c0ead688a
        # AsyncMessageStream inherits from AsyncStream, so patching Stream is sufficient on these versions.
        if not issubclass(AsyncMessageStream, AsyncStream):
            AsyncMessageStream.close = _wrap_async_close(AsyncMessageStream.close)


def _capture_exception(exc: "Any") -> None:
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "anthropic", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _get_token_usage(result: "Messages") -> "tuple[int, int, int, int]":
    """
    Get token usage from the Anthropic response.
    Returns: (input_tokens, output_tokens, cache_read_input_tokens, cache_write_input_tokens)
    """
    input_tokens = 0
    output_tokens = 0
    cache_read_input_tokens = 0
    cache_write_input_tokens = 0
    if hasattr(result, "usage"):
        usage = result.usage
        if hasattr(usage, "input_tokens") and isinstance(usage.input_tokens, int):
            input_tokens = usage.input_tokens
        if hasattr(usage, "output_tokens") and isinstance(usage.output_tokens, int):
            output_tokens = usage.output_tokens
        if hasattr(usage, "cache_read_input_tokens") and isinstance(
            usage.cache_read_input_tokens, int
        ):
            cache_read_input_tokens = usage.cache_read_input_tokens
        if hasattr(usage, "cache_creation_input_tokens") and isinstance(
            usage.cache_creation_input_tokens, int
        ):
            cache_write_input_tokens = usage.cache_creation_input_tokens

    # Anthropic's input_tokens excludes cached/cache_write tokens.
    # Normalize to total input tokens so downstream cost calculations
    # (input_tokens - cached) don't produce negative values.
    input_tokens += cache_read_input_tokens + cache_write_input_tokens

    return (
        input_tokens,
        output_tokens,
        cache_read_input_tokens,
        cache_write_input_tokens,
    )


def _collect_ai_data(
    event: "MessageStreamEvent",
    model: "str | None",
    usage: "_RecordedUsage",
    content_blocks: "list[str]",
    response_id: "str | None" = None,
    finish_reason: "str | None" = None,
) -> "tuple[str | None, _RecordedUsage, list[str], str | None, str | None]":
    """
    Collect model information, token usage, and collect content blocks from the AI streaming response.
    """
    with capture_internal_exceptions():
        if hasattr(event, "type"):
            if event.type == "content_block_start":
                pass
            elif event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    content_blocks.append(event.delta.text)
                elif hasattr(event.delta, "partial_json"):
                    content_blocks.append(event.delta.partial_json)
            elif event.type == "content_block_stop":
                pass

            # Token counting logic mirrors anthropic SDK, which also extracts already accumulated tokens.
            # https://github.com/anthropics/anthropic-sdk-python/blob/9c485f6966e10ae0ea9eabb3a921d2ea8145a25b/src/anthropic/lib/streaming/_messages.py#L433-L518
            if event.type == "message_start":
                model = event.message.model or model
                response_id = event.message.id

                incoming_usage = event.message.usage
                usage.output_tokens = incoming_usage.output_tokens
                usage.input_tokens = incoming_usage.input_tokens

                usage.cache_write_input_tokens = getattr(
                    incoming_usage, "cache_creation_input_tokens", None
                )
                usage.cache_read_input_tokens = getattr(
                    incoming_usage, "cache_read_input_tokens", None
                )

                return (
                    model,
                    usage,
                    content_blocks,
                    response_id,
                    finish_reason,
                )

            # Counterintuitive, but message_delta contains cumulative token counts :)
            if event.type == "message_delta":
                usage.output_tokens = event.usage.output_tokens

                # Update other usage fields if they exist in the event
                input_tokens = getattr(event.usage, "input_tokens", None)
                if input_tokens is not None:
                    usage.input_tokens = input_tokens

                cache_creation_input_tokens = getattr(
                    event.usage, "cache_creation_input_tokens", None
                )
                if cache_creation_input_tokens is not None:
                    usage.cache_write_input_tokens = cache_creation_input_tokens

                cache_read_input_tokens = getattr(
                    event.usage, "cache_read_input_tokens", None
                )
                if cache_read_input_tokens is not None:
                    usage.cache_read_input_tokens = cache_read_input_tokens
                # TODO: Record event.usage.server_tool_use

                if event.delta.stop_reason is not None:
                    finish_reason = event.delta.stop_reason

                return (model, usage, content_blocks, response_id, finish_reason)

    return (
        model,
        usage,
        content_blocks,
        response_id,
        finish_reason,
    )


def _transform_anthropic_content_block(
    content_block: "dict[str, Any]",
) -> "dict[str, Any]":
    """
    Transform an Anthropic content block using the Anthropic-specific transformer,
    with special handling for Anthropic's text-type documents.
    """
    # Handle Anthropic's text-type documents specially (not covered by shared function)
    if content_block.get("type") == "document":
        source = content_block.get("source")
        if isinstance(source, dict) and source.get("type") == "text":
            return {
                "type": "text",
                "text": source.get("data", ""),
            }

    # Use Anthropic-specific transformation
    result = transform_anthropic_content_part(content_block)
    return result if result is not None else content_block


def _transform_system_instructions(
    system_instructions: "Union[str, Iterable[TextBlockParam]]",
) -> "list[TextPart]":
    if isinstance(system_instructions, str):
        return [
            {
                "type": "text",
                "content": system_instructions,
            }
        ]

    return [
        {
            "type": "text",
            "content": instruction["text"],
        }
        for instruction in system_instructions
        if isinstance(instruction, dict) and "text" in instruction
    ]


def _set_common_input_data(
    span: "Span",
    integration: "AnthropicIntegration",
    max_tokens: "int",
    messages: "Iterable[MessageParam]",
    model: "ModelParam",
    system: "Optional[Union[str, Iterable[TextBlockParam]]]",
    temperature: "Optional[float]",
    top_k: "Optional[int]",
    top_p: "Optional[float]",
    tools: "Optional[Iterable[ToolUnionParam]]",
) -> None:
    """
    Set input data for the span based on the provided keyword arguments for the anthropic message creation.
    """
    span.set_data(SPANDATA.GEN_AI_SYSTEM, "anthropic")
    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
    if (
        messages is not None
        and len(messages) > 0  # type: ignore
        and should_send_default_pii()
        and integration.include_prompts
    ):
        if isinstance(system, str) or isinstance(system, Iterable):
            span.set_data(
                SPANDATA.GEN_AI_SYSTEM_INSTRUCTIONS,
                json.dumps(_transform_system_instructions(system)),
            )

        normalized_messages = []
        for message in messages:
            if (
                message.get("role") == GEN_AI_ALLOWED_MESSAGE_ROLES.USER
                and "content" in message
                and isinstance(message["content"], (list, tuple))
            ):
                transformed_content = []
                for item in message["content"]:
                    # Skip tool_result items - they can contain images/documents
                    # with nested structures that are difficult to redact properly
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        continue

                    # Transform content blocks (images, documents, etc.)
                    transformed_content.append(
                        _transform_anthropic_content_block(item)
                        if isinstance(item, dict)
                        else item
                    )

                # If there are non-tool-result items, add them as a message
                if transformed_content:
                    normalized_messages.append(
                        {
                            "role": message.get("role"),
                            "content": transformed_content,
                        }
                    )
            else:
                # Transform content for non-list messages or assistant messages
                transformed_message = message.copy()
                if "content" in transformed_message:
                    content = transformed_message["content"]
                    if isinstance(content, (list, tuple)):
                        transformed_message["content"] = [
                            _transform_anthropic_content_block(item)
                            if isinstance(item, dict)
                            else item
                            for item in content
                        ]
                normalized_messages.append(transformed_message)

        role_normalized_messages = normalize_message_roles(normalized_messages)

        client = sentry_sdk.get_client()
        scope = sentry_sdk.get_current_scope()
        messages_data = (
            role_normalized_messages
            if client.options.get("stream_gen_ai_spans", False)
            else truncate_and_annotate_messages(role_normalized_messages, span, scope)
        )
        if messages_data is not None:
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages_data, unpack=False
            )

    if max_tokens is not None and _is_given(max_tokens):
        span.set_data(SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)
    if model is not None and _is_given(model):
        span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)
    if temperature is not None and _is_given(temperature):
        span.set_data(SPANDATA.GEN_AI_REQUEST_TEMPERATURE, temperature)
    if top_k is not None and _is_given(top_k):
        span.set_data(SPANDATA.GEN_AI_REQUEST_TOP_K, top_k)
    if top_p is not None and _is_given(top_p):
        span.set_data(SPANDATA.GEN_AI_REQUEST_TOP_P, top_p)

    if tools is not None and _is_given(tools) and len(tools) > 0:  # type: ignore
        span.set_data(SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools))


def _set_create_input_data(
    span: "Span", kwargs: "dict[str, Any]", integration: "AnthropicIntegration"
) -> None:
    """
    Set input data for the span based on the provided keyword arguments for the anthropic message creation.
    """
    span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, kwargs.get("stream", False))

    _set_common_input_data(
        span=span,
        integration=integration,
        max_tokens=kwargs.get("max_tokens"),  # type: ignore
        messages=kwargs.get("messages"),  # type: ignore
        model=kwargs.get("model"),
        system=kwargs.get("system"),
        temperature=kwargs.get("temperature"),
        top_k=kwargs.get("top_k"),
        top_p=kwargs.get("top_p"),
        tools=kwargs.get("tools"),
    )


def _wrap_synchronous_message_iterator(
    stream: "Union[Stream, MessageStream]",
    iterator: "Iterator[Union[RawMessageStreamEvent, MessageStreamEvent]]",
) -> "Iterator[Union[RawMessageStreamEvent, MessageStreamEvent]]":
    """
    Sets information received while iterating the response stream on the AI Client Span.
    Responsible for closing the AI Client Span unless the span has already been closed in the close() patch.
    """
    with _StreamSpanContext(stream, guaranteed_streaming_state=True):
        for event in iterator:
            # Message and content types are aliases for corresponding Raw* types, introduced in
            # https://github.com/anthropics/anthropic-sdk-python/commit/bc9d11cd2addec6976c46db10b7c89a8c276101a
            if not isinstance(
                event,
                (
                    MessageStartEvent,
                    MessageDeltaEvent,
                    MessageStopEvent,
                    ContentBlockStartEvent,
                    ContentBlockDeltaEvent,
                    ContentBlockStopEvent,
                ),
            ):
                yield event
                continue

            _accumulate_event_data(stream, event)
            yield event


async def _wrap_asynchronous_message_iterator(
    stream: "Union[AsyncStream, AsyncMessageStream]",
    iterator: "AsyncIterator[Union[RawMessageStreamEvent, MessageStreamEvent]]",
) -> "AsyncIterator[Union[RawMessageStreamEvent, MessageStreamEvent]]":
    """
    Sets information received while iterating the response stream on the AI Client Span.
    Responsible for closing the AI Client Span unless the span has already been closed in the close() patch.
    """
    with _StreamSpanContext(stream, guaranteed_streaming_state=True):
        async for event in iterator:
            # Message and content types are aliases for corresponding Raw* types, introduced in
            # https://github.com/anthropics/anthropic-sdk-python/commit/bc9d11cd2addec6976c46db10b7c89a8c276101a
            if not isinstance(
                event,
                (
                    MessageStartEvent,
                    MessageDeltaEvent,
                    MessageStopEvent,
                    ContentBlockStartEvent,
                    ContentBlockDeltaEvent,
                    ContentBlockStopEvent,
                ),
            ):
                yield event
                continue

            _accumulate_event_data(stream, event)
            yield event


def _set_output_data(
    span: "Span",
    integration: "AnthropicIntegration",
    model: "str | None",
    input_tokens: "int | None",
    output_tokens: "int | None",
    cache_read_input_tokens: "int | None",
    cache_write_input_tokens: "int | None",
    content_blocks: "list[Any]",
    response_id: "str | None" = None,
    finish_reason: "str | None" = None,
) -> None:
    """
    Set output data for the span based on the AI response."""
    span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, model)
    if response_id is not None:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_ID, response_id)
    if finish_reason is not None:
        span.set_data(SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, [finish_reason])
    if should_send_default_pii() and integration.include_prompts:
        output_messages: "dict[str, list[Any]]" = {
            "response": [],
            "tool": [],
        }

        for output in content_blocks:
            if output["type"] == "text":
                output_messages["response"].append(output["text"])
            elif output["type"] == "tool_use":
                output_messages["tool"].append(output)

        if len(output_messages["tool"]) > 0:
            set_data_normalized(
                span,
                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                output_messages["tool"],
                unpack=False,
            )

        if len(output_messages["response"]) > 0:
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, output_messages["response"]
            )

    record_token_usage(
        span,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_tokens_cached=cache_read_input_tokens,
        input_tokens_cache_write=cache_write_input_tokens,
    )


def _sentry_patched_create_sync(f: "Any", *args: "Any", **kwargs: "Any") -> "Any":
    """
    Creates and manages an AI Client Span for both non-streaming and streaming calls.
    """
    integration = kwargs.pop("integration")
    if integration is None:
        return f(*args, **kwargs)

    if "messages" not in kwargs:
        return f(*args, **kwargs)

    try:
        iter(kwargs["messages"])
    except TypeError:
        return f(*args, **kwargs)

    model = kwargs.get("model", "")

    span = get_start_span_function()(
        op=OP.GEN_AI_CHAT,
        name=f"chat {model}".strip(),
        origin=AnthropicIntegration.origin,
    )
    span.__enter__()

    _set_create_input_data(span, kwargs, integration)

    try:
        result = f(*args, **kwargs)
    except Exception as exc:
        exc_info = sys.exc_info()
        with capture_internal_exceptions():
            _capture_exception(exc)
            span.__exit__(*exc_info)
        reraise(*exc_info)

    if isinstance(result, Stream):
        result._span = span
        result._integration = integration

        _initialize_data_accumulation_state(result)
        result._iterator = _wrap_synchronous_message_iterator(
            result,
            result._iterator,
        )

        return result

    with capture_internal_exceptions():
        if hasattr(result, "content"):
            (
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_write_input_tokens,
            ) = _get_token_usage(result)

            content_blocks = []
            for content_block in result.content:
                if hasattr(content_block, "to_dict"):
                    content_blocks.append(content_block.to_dict())
                elif hasattr(content_block, "model_dump"):
                    content_blocks.append(content_block.model_dump())
                elif hasattr(content_block, "text"):
                    content_blocks.append({"type": "text", "text": content_block.text})

            _set_output_data(
                span=span,
                integration=integration,
                model=getattr(result, "model", None),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
                content_blocks=content_blocks,
                response_id=getattr(result, "id", None),
                finish_reason=getattr(result, "stop_reason", None),
            )
            span.__exit__(None, None, None)
        else:
            span.set_data("unknown_response", True)
            span.__exit__(None, None, None)

    return result


async def _sentry_patched_create_async(
    f: "Any", *args: "Any", **kwargs: "Any"
) -> "Any":
    """
    Creates and manages an AI Client Span for both non-streaming and streaming calls.
    """
    integration = kwargs.pop("integration")
    if integration is None:
        return await f(*args, **kwargs)

    if "messages" not in kwargs:
        return await f(*args, **kwargs)

    try:
        iter(kwargs["messages"])
    except TypeError:
        return await f(*args, **kwargs)

    model = kwargs.get("model", "")

    span = get_start_span_function()(
        op=OP.GEN_AI_CHAT,
        name=f"chat {model}".strip(),
        origin=AnthropicIntegration.origin,
    )
    span.__enter__()

    _set_create_input_data(span, kwargs, integration)

    try:
        result = await f(*args, **kwargs)
    except Exception as exc:
        exc_info = sys.exc_info()
        with capture_internal_exceptions():
            _capture_exception(exc)
            span.__exit__(*exc_info)
        reraise(*exc_info)

    if isinstance(result, AsyncStream):
        result._span = span
        result._integration = integration

        _initialize_data_accumulation_state(result)
        result._iterator = _wrap_asynchronous_message_iterator(
            result,
            result._iterator,
        )

        return result

    with capture_internal_exceptions():
        if hasattr(result, "content"):
            (
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_write_input_tokens,
            ) = _get_token_usage(result)

            content_blocks = []
            for content_block in result.content:
                if hasattr(content_block, "to_dict"):
                    content_blocks.append(content_block.to_dict())
                elif hasattr(content_block, "model_dump"):
                    content_blocks.append(content_block.model_dump())
                elif hasattr(content_block, "text"):
                    content_blocks.append({"type": "text", "text": content_block.text})

            _set_output_data(
                span=span,
                integration=integration,
                model=getattr(result, "model", None),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_write_input_tokens=cache_write_input_tokens,
                content_blocks=content_blocks,
                response_id=getattr(result, "id", None),
                finish_reason=getattr(result, "stop_reason", None),
            )
            span.__exit__(None, None, None)
        else:
            span.set_data("unknown_response", True)
            span.__exit__(None, None, None)

    return result


def _wrap_message_create(f: "Any") -> "Any":
    @wraps(f)
    def _sentry_wrapped_create_sync(*args: "Any", **kwargs: "Any") -> "Any":
        integration = sentry_sdk.get_client().get_integration(AnthropicIntegration)
        kwargs["integration"] = integration

        return _sentry_patched_create_sync(f, *args, **kwargs)

    return _sentry_wrapped_create_sync


def _initialize_data_accumulation_state(stream: "Union[Stream, MessageStream]") -> None:
    """
    Initialize fields for accumulating output on the Stream instance.
    """
    if not hasattr(stream, "_model"):
        stream._model = None
        stream._usage = _RecordedUsage()
        stream._content_blocks = []
        stream._response_id = None
        stream._finish_reason = None


def _accumulate_event_data(
    stream: "Union[Stream, MessageStream]",
    event: "Union[RawMessageStreamEvent, MessageStreamEvent]",
) -> None:
    """
    Update accumulated output from a single stream event.
    """
    (model, usage, content_blocks, response_id, finish_reason) = _collect_ai_data(
        event,
        stream._model,
        stream._usage,
        stream._content_blocks,
        stream._response_id,
        stream._finish_reason,
    )

    stream._model = model
    stream._usage = usage
    stream._content_blocks = content_blocks
    stream._response_id = response_id
    stream._finish_reason = finish_reason


def _set_streaming_output_data(
    span: "Span",
    integration: "AnthropicIntegration",
    model: "Optional[str]",
    usage: "_RecordedUsage",
    content_blocks: "list[str]",
    response_id: "Optional[str]",
    finish_reason: "Optional[str]",
) -> None:
    """
    Set output attributes on the AI Client Span.
    """
    # Anthropic's input_tokens excludes cached/cache_write tokens.
    # Normalize to total input tokens for correct cost calculations.
    total_input = (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_write_input_tokens or 0)
    )

    _set_output_data(
        span=span,
        integration=integration,
        model=model,
        input_tokens=total_input,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_write_input_tokens=usage.cache_write_input_tokens,
        content_blocks=[{"text": "".join(content_blocks), "type": "text"}],
        response_id=response_id,
        finish_reason=finish_reason,
    )


def _wrap_close(
    f: "Callable[..., None]",
) -> "Callable[..., None]":
    """
    Closes the AI Client Span unless the finally block in `_wrap_synchronous_message_iterator()` runs first.
    """

    def close(self: "Union[Stream, MessageStream]") -> None:
        with _StreamSpanContext(self):
            return f(self)

    return close


def _wrap_message_create_async(f: "Any") -> "Any":
    @wraps(f)
    async def _sentry_wrapped_create_async(*args: "Any", **kwargs: "Any") -> "Any":
        integration = sentry_sdk.get_client().get_integration(AnthropicIntegration)
        kwargs["integration"] = integration

        return await _sentry_patched_create_async(f, *args, **kwargs)

    return _sentry_wrapped_create_async


def _wrap_async_close(
    f: "Callable[..., Awaitable[None]]",
) -> "Callable[..., Awaitable[None]]":
    """
    Closes the AI Client Span unless the finally block in `_wrap_asynchronous_message_iterator()` runs first.
    """

    async def close(self: "AsyncStream") -> None:
        with _StreamSpanContext(self):
            return await f(self)

    return close


def _wrap_message_stream(f: "Any") -> "Any":
    """
    Attaches user-provided arguments to the returned context manager.
    The attributes are set on AI Client Spans in the patch for the context manager.
    """

    @wraps(f)
    def _sentry_patched_stream(*args: "Any", **kwargs: "Any") -> "MessageStreamManager":
        stream_manager = f(*args, **kwargs)

        stream_manager._max_tokens = kwargs.get("max_tokens")
        stream_manager._messages = kwargs.get("messages")
        stream_manager._model = kwargs.get("model")
        stream_manager._system = kwargs.get("system")
        stream_manager._temperature = kwargs.get("temperature")
        stream_manager._top_k = kwargs.get("top_k")
        stream_manager._top_p = kwargs.get("top_p")
        stream_manager._tools = kwargs.get("tools")

        return stream_manager

    return _sentry_patched_stream


def _wrap_message_stream_manager_enter(f: "Any") -> "Any":
    """
    Creates and manages AI Client Spans.
    """

    @wraps(f)
    def _sentry_patched_enter(self: "MessageStreamManager") -> "MessageStream":
        if not hasattr(self, "_max_tokens"):
            return f(self)

        integration = sentry_sdk.get_client().get_integration(AnthropicIntegration)

        if integration is None:
            return f(self)

        if self._messages is None:
            return f(self)

        try:
            iter(self._messages)
        except TypeError:
            return f(self)

        span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name="chat" if self._model is None else f"chat {self._model}".strip(),
            origin=AnthropicIntegration.origin,
        )
        span.__enter__()

        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)
        _set_common_input_data(
            span=span,
            integration=integration,
            max_tokens=self._max_tokens,
            messages=self._messages,
            model=self._model,
            system=self._system,
            temperature=self._temperature,
            top_k=self._top_k,
            top_p=self._top_p,
            tools=self._tools,
        )

        try:
            stream = f(self)
        except Exception as exc:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(exc)
                span.__exit__(*exc_info)
            reraise(*exc_info)

        stream._span = span
        stream._integration = integration

        _initialize_data_accumulation_state(stream)
        stream._iterator = _wrap_synchronous_message_iterator(
            stream,
            stream._iterator,
        )

        return stream

    return _sentry_patched_enter


def _wrap_async_message_stream(f: "Any") -> "Any":
    """
    Attaches user-provided arguments to the returned context manager.
    The attributes are set on AI Client Spans in the patch for the context manager.
    """

    @wraps(f)
    def _sentry_patched_stream(
        *args: "Any", **kwargs: "Any"
    ) -> "AsyncMessageStreamManager":
        stream_manager = f(*args, **kwargs)

        stream_manager._max_tokens = kwargs.get("max_tokens")
        stream_manager._messages = kwargs.get("messages")
        stream_manager._model = kwargs.get("model")
        stream_manager._system = kwargs.get("system")
        stream_manager._temperature = kwargs.get("temperature")
        stream_manager._top_k = kwargs.get("top_k")
        stream_manager._top_p = kwargs.get("top_p")
        stream_manager._tools = kwargs.get("tools")

        return stream_manager

    return _sentry_patched_stream


def _wrap_async_message_stream_manager_aenter(f: "Any") -> "Any":
    """
    Creates and manages AI Client Spans.
    """

    @wraps(f)
    async def _sentry_patched_aenter(
        self: "AsyncMessageStreamManager",
    ) -> "AsyncMessageStream":
        if not hasattr(self, "_max_tokens"):
            return await f(self)

        integration = sentry_sdk.get_client().get_integration(AnthropicIntegration)

        if integration is None:
            return await f(self)

        if self._messages is None:
            return await f(self)

        try:
            iter(self._messages)
        except TypeError:
            return await f(self)

        span = get_start_span_function()(
            op=OP.GEN_AI_CHAT,
            name="chat" if self._model is None else f"chat {self._model}".strip(),
            origin=AnthropicIntegration.origin,
        )
        span.__enter__()

        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)
        _set_common_input_data(
            span=span,
            integration=integration,
            max_tokens=self._max_tokens,
            messages=self._messages,
            model=self._model,
            system=self._system,
            temperature=self._temperature,
            top_k=self._top_k,
            top_p=self._top_p,
            tools=self._tools,
        )

        try:
            stream = await f(self)
        except Exception as exc:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(exc)
                span.__exit__(*exc_info)
            reraise(*exc_info)

        stream._span = span
        stream._integration = integration

        _initialize_data_accumulation_state(stream)
        stream._iterator = _wrap_asynchronous_message_iterator(
            stream,
            stream._iterator,
        )

        return stream

    return _sentry_patched_aenter


def _is_given(obj: "Any") -> bool:
    """
    Check for givenness safely across different anthropic versions.
    """
    if NotGiven is not None and isinstance(obj, NotGiven):
        return False
    if Omit is not None and isinstance(obj, Omit):
        return False
    return True
