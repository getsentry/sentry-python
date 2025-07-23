from functools import wraps

import sentry_sdk
from sentry_sdk import consts
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    safe_serialize,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Iterable, List, Optional, Callable, AsyncIterator, Iterator
    from sentry_sdk.tracing import Span

try:
    from openai.resources.chat.completions import Completions, AsyncCompletions
    from openai.resources import Embeddings, AsyncEmbeddings

    if TYPE_CHECKING:
        from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk
except ImportError:
    raise DidNotEnable("OpenAI not installed")

RESPONSES_API_ENABLED = True
try:
    # responses API support was introduced in v1.66.0
    from openai.resources.responses import Responses, AsyncResponses
except ImportError:
    RESPONSES_API_ENABLED = False


class OpenAIIntegration(Integration):
    identifier = "openai"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True, tiktoken_encoding_name=None):
        # type: (OpenAIIntegration, bool, Optional[str]) -> None
        self.include_prompts = include_prompts

        self.tiktoken_encoding = None
        if tiktoken_encoding_name is not None:
            import tiktoken  # type: ignore

            self.tiktoken_encoding = tiktoken.get_encoding(tiktoken_encoding_name)

    @staticmethod
    def setup_once():
        # type: () -> None
        Completions.create = _wrap_chat_completion_create(Completions.create)
        AsyncCompletions.create = _wrap_async_chat_completion_create(
            AsyncCompletions.create
        )

        Embeddings.create = _wrap_embeddings_create(Embeddings.create)
        AsyncEmbeddings.create = _wrap_async_embeddings_create(AsyncEmbeddings.create)

        if RESPONSES_API_ENABLED:
            Responses.create = _wrap_responses_create(Responses.create)
            AsyncResponses.create = _wrap_async_responses_create(AsyncResponses.create)

    def count_tokens(self, s):
        # type: (OpenAIIntegration, str) -> int
        if self.tiktoken_encoding is not None:
            return len(self.tiktoken_encoding.encode_ordinary(s))
        return 0


def _capture_exception(exc):
    # type: (Any) -> None
    # Close an eventually open span
    # We need to do this by hand because we are not using the start_span context manager
    current_span = sentry_sdk.get_current_span()
    if current_span is not None:
        current_span.__exit__(None, None, None)

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _get_usage(usage, names):
    # type: (Any, List[str]) -> int
    for name in names:
        if hasattr(usage, name) and isinstance(getattr(usage, name), int):
            return getattr(usage, name)
    return 0


def _calculate_token_usage(
    messages, response, span, streaming_message_responses, count_tokens
):
    # type: (Iterable[ChatCompletionMessageParam], Any, Span, Optional[List[str]], Callable[..., Any]) -> None
    input_tokens = 0  # type: Optional[int]
    input_tokens_cached = 0  # type: Optional[int]
    output_tokens = 0  # type: Optional[int]
    output_tokens_reasoning = 0  # type: Optional[int]
    total_tokens = 0  # type: Optional[int]

    if hasattr(response, "usage"):
        input_tokens = _get_usage(response.usage, ["input_tokens", "prompt_tokens"])
        if hasattr(response.usage, "input_tokens_details"):
            input_tokens_cached = _get_usage(
                response.usage.input_tokens_details, ["cached_tokens"]
            )

        output_tokens = _get_usage(
            response.usage, ["output_tokens", "completion_tokens"]
        )
        if hasattr(response.usage, "output_tokens_details"):
            output_tokens_reasoning = _get_usage(
                response.usage.output_tokens_details, ["reasoning_tokens"]
            )

        total_tokens = _get_usage(response.usage, ["total_tokens"])

    # Manually count tokens
    if input_tokens == 0:
        for message in messages:
            if "content" in message:
                input_tokens += count_tokens(message["content"])

    if output_tokens == 0:
        if streaming_message_responses is not None:
            for message in streaming_message_responses:
                output_tokens += count_tokens(message)
        elif hasattr(response, "choices"):
            for choice in response.choices:
                if hasattr(choice, "message"):
                    output_tokens += count_tokens(choice.message)

    # Do not set token data if it is 0
    input_tokens = input_tokens or None
    input_tokens_cached = input_tokens_cached or None
    output_tokens = output_tokens or None
    output_tokens_reasoning = output_tokens_reasoning or None
    total_tokens = total_tokens or None

    record_token_usage(
        span,
        input_tokens=input_tokens,
        input_tokens_cached=input_tokens_cached,
        output_tokens=output_tokens,
        output_tokens_reasoning=output_tokens_reasoning,
        total_tokens=total_tokens,
    )


def _set_request_data(span, kwargs, operation, integration):
    # type: (Span, dict[str, Any], str, Integration) -> None
    messages = kwargs.get("messages")
    if messages is None:
        messages = kwargs.get("input")

    if isinstance(messages, str):
        messages = [messages]

    if (
        messages is not None
        and len(messages) > 0
        and should_send_default_pii()
        and integration.include_prompts
    ):
        set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages)

    # Common attributes
    model = kwargs.get("model")
    streaming = kwargs.get("stream")
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "openai")
    set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MODEL, model)
    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, operation)
    set_data_normalized(span, SPANDATA.AI_STREAMING, streaming)

    # Optional attributes
    max_tokens = kwargs.get("max_tokens")
    if max_tokens is not None:
        set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)

    presence_penalty = kwargs.get("presence_penalty")
    if presence_penalty is not None:
        set_data_normalized(
            span, SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY, presence_penalty
        )

    temperature = kwargs.get("temperature")
    if temperature is not None:
        set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_TEMPERATURE, temperature)

    top_p = kwargs.get("top_p")
    if top_p is not None:
        set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_TOP_P, top_p)


def _set_response_data(span, response, kwargs, integration):
    # type: (Span, Any, dict[str, Any], Integration) -> None
    if hasattr(response, "model"):
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_MODEL, response.model)

    messages = kwargs.get("messages")
    if messages is None:
        messages = kwargs.get("input")

    if isinstance(messages, str):
        messages = [messages]

    if hasattr(response, "choices"):
        if should_send_default_pii() and integration.include_prompts:
            response_text = [choice.message.dict() for choice in response.choices]
            if len(response_text) > 0:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    safe_serialize(response_text),
                )

    elif hasattr(response, "output"):
        if should_send_default_pii() and integration.include_prompts:
            response_text = [item.to_dict() for item in response.output]
            if len(response_text) > 0:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    safe_serialize(response_text),
                )

    elif hasattr(response, "_iterator"):
        data_buf: list[list[str]] = []  # one for each choice

        old_iterator = response._iterator

        def new_iterator():
            # type: () -> Iterator[ChatCompletionChunk]
            with capture_internal_exceptions():
                for x in old_iterator:
                    if hasattr(x, "choices"):
                        choice_index = 0
                        for choice in x.choices:
                            if hasattr(choice, "delta") and hasattr(
                                choice.delta, "content"
                            ):
                                content = choice.delta.content
                                if len(data_buf) <= choice_index:
                                    data_buf.append([])
                                data_buf[choice_index].append(content or "")
                            choice_index += 1
                    yield x
                if len(data_buf) > 0:
                    all_responses = list(map(lambda chunk: "".join(chunk), data_buf))
                    if should_send_default_pii() and integration.include_prompts:
                        set_data_normalized(
                            span, SPANDATA.GEN_AI_RESPONSE_TEXT, all_responses
                        )
                    _calculate_token_usage(
                        messages,
                        response,
                        span,
                        all_responses,
                        integration.count_tokens,
                    )
            span.__exit__(None, None, None)

        async def new_iterator_async():
            # type: () -> AsyncIterator[ChatCompletionChunk]
            with capture_internal_exceptions():
                async for x in old_iterator:
                    if hasattr(x, "choices"):
                        choice_index = 0
                        for choice in x.choices:
                            if hasattr(choice, "delta") and hasattr(
                                choice.delta, "content"
                            ):
                                content = choice.delta.content
                                if len(data_buf) <= choice_index:
                                    data_buf.append([])
                                data_buf[choice_index].append(content or "")
                            choice_index += 1
                    yield x
                if len(data_buf) > 0:
                    all_responses = list(map(lambda chunk: "".join(chunk), data_buf))
                    if should_send_default_pii() and integration.include_prompts:
                        set_data_normalized(
                            span, SPANDATA.GEN_AI_RESPONSE_TEXT, all_responses
                        )
                    _calculate_token_usage(
                        messages,
                        response,
                        span,
                        all_responses,
                        integration.count_tokens,
                    )
            span.__exit__(None, None, None)

        if str(type(response._iterator)) == "<class 'async_generator'>":
            response._iterator = new_iterator_async()
        else:
            response._iterator = new_iterator()

    _calculate_token_usage(messages, response, span, None, integration.count_tokens)


def _new_chat_completion_common(f, *args, **kwargs):
    # type: (Any, Any, Any) -> Any
    integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
    if integration is None:
        return f(*args, **kwargs)

    if "messages" not in kwargs:
        # invalid call (in all versions of openai), let it return error
        return f(*args, **kwargs)

    try:
        iter(kwargs["messages"])
    except TypeError:
        # invalid call (in all versions), messages must be iterable
        return f(*args, **kwargs)

    model = kwargs.get("model")
    operation = "chat"

    with sentry_sdk.start_span(
        op=consts.OP.GEN_AI_CHAT,
        name=f"{operation} {model}",
        origin=OpenAIIntegration.origin,
    ) as span:
        _set_request_data(span, kwargs, operation, integration)

        response = yield f, args, kwargs

        _set_response_data(span, response, kwargs, integration)

    return response


def _wrap_chat_completion_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    def _execute_sync(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_chat_completion_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return e.value

        try:
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    def _sentry_patched_create_sync(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None or "messages" not in kwargs:
            # no "messages" means invalid call (in all versions of openai), let it return error
            return f(*args, **kwargs)

        return _execute_sync(f, *args, **kwargs)

    return _sentry_patched_create_sync


def _wrap_async_chat_completion_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    async def _execute_async(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_chat_completion_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return await e.value

        try:
            try:
                result = await f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    async def _sentry_patched_create_async(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None or "messages" not in kwargs:
            # no "messages" means invalid call (in all versions of openai), let it return error
            return await f(*args, **kwargs)

        return await _execute_async(f, *args, **kwargs)

    return _sentry_patched_create_async


def _new_embeddings_create_common(f, *args, **kwargs):
    # type: (Any, Any, Any) -> Any
    integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
    if integration is None:
        return f(*args, **kwargs)

    model = kwargs.get("model")
    operation = "embeddings"

    with sentry_sdk.start_span(
        op=consts.OP.GEN_AI_EMBEDDINGS,
        name=f"{operation} {model}",
        origin=OpenAIIntegration.origin,
    ) as span:
        _set_request_data(span, kwargs, operation, integration)

        response = yield f, args, kwargs

        _set_response_data(span, response, kwargs, integration)

        return response


def _wrap_embeddings_create(f):
    # type: (Any) -> Any
    def _execute_sync(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_embeddings_create_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return e.value

        try:
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    def _sentry_patched_create_sync(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None:
            return f(*args, **kwargs)

        return _execute_sync(f, *args, **kwargs)

    return _sentry_patched_create_sync


def _wrap_async_embeddings_create(f):
    # type: (Any) -> Any
    async def _execute_async(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_embeddings_create_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return await e.value

        try:
            try:
                result = await f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    async def _sentry_patched_create_async(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None:
            return await f(*args, **kwargs)

        return await _execute_async(f, *args, **kwargs)

    return _sentry_patched_create_async


def _new_responses_create_common(f, *args, **kwargs):
    # type: (Any, Any, Any) -> Any
    integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
    if integration is None:
        return f(*args, **kwargs)

    model = kwargs.get("model")
    operation = "responses"

    with sentry_sdk.start_span(
        op=consts.OP.GEN_AI_RESPONSES,
        name=f"{operation} {model}",
        origin=OpenAIIntegration.origin,
    ) as span:
        _set_request_data(span, kwargs, operation, integration)

        response = yield f, args, kwargs

        _set_response_data(span, response, kwargs, integration)

        return response


def _wrap_responses_create(f):
    # type: (Any) -> Any
    def _execute_sync(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_responses_create_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return e.value

        try:
            try:
                result = f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    def _sentry_patched_create_sync(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None:
            return f(*args, **kwargs)

        return _execute_sync(f, *args, **kwargs)

    return _sentry_patched_create_sync


def _wrap_async_responses_create(f):
    # type: (Any) -> Any
    async def _execute_async(f, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        gen = _new_responses_create_common(f, *args, **kwargs)

        try:
            f, args, kwargs = next(gen)
        except StopIteration as e:
            return await e.value

        try:
            try:
                result = await f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

            return gen.send(result)
        except StopIteration as e:
            return e.value

    @wraps(f)
    async def _sentry_patched_responses_async(*args, **kwargs):
        # type: (Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
        if integration is None:
            return await f(*args, **kwargs)

        return await _execute_async(f, *args, **kwargs)

    return _sentry_patched_responses_async
