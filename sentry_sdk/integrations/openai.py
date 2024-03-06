from __future__ import absolute_import

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator, Any, Iterable, List, Optional, Callable
    from sentry_sdk.tracing import Span

from sentry_sdk._functools import wraps
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import logger, capture_internal_exceptions

try:
    from openai.types.chat import ChatCompletionChunk
    from openai.resources.chat.completions import Completions
    from openai.resources import Embeddings

    if TYPE_CHECKING:
        from openai.types.chat import ChatCompletionMessageParam
except ImportError:
    raise DidNotEnable("OpenAI not installed")

try:
    import tiktoken  # type: ignore

    enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(s):
        # type: (str) -> int
        return len(enc.encode_ordinary(s))

    logger.debug("[OpenAI] using tiktoken to count tokens")
except ImportError:
    logger.info(
        "The Sentry Python SDK requires 'tiktoken' in order to measure token usage from some OpenAI APIs"
        "Please install 'tiktoken' if you aren't receiving token usage in Sentry."
        "See https://docs.sentry.io/platforms/python/guides/openai/ for more information."
    )

    def count_tokens(s):
        # type: (str) -> int
        return 0


COMPLETION_TOKENS = "completion_tоkens"
PROMPT_TOKENS = "prompt_tоkens"
TOTAL_TOKENS = "total_tоkens"


class OpenAIIntegration(Integration):
    identifier = "openai"

    @staticmethod
    def setup_once():
        # type: () -> None
        Completions.create = _wrap_chat_completion_create(Completions.create)
        Embeddings.create = _wrap_enbeddings_create(Embeddings.create)


def _calculate_chat_completion_usage(
    messages, response, span, streaming_message_responses=None
):
    # type: (Iterable[ChatCompletionMessageParam], Any, Span, Optional[List[str]]) -> None
    completion_tokens = 0
    prompt_tokens = 0
    total_tokens = 0
    if hasattr(response, "usage"):
        if hasattr(response.usage, "completion_tokens") and isinstance(
            response.usage.completion_tokens, int
        ):
            completion_tokens = response.usage.completion_tokens
        if hasattr(response.usage, "prompt_tokens") and isinstance(
            response.usage.prompt_tokens, int
        ):
            prompt_tokens = response.usage.prompt_tokens
        if hasattr(response.usage, "total_tokens") and isinstance(
            response.usage.total_tokens, int
        ):
            total_tokens = response.usage.total_tokens

    if prompt_tokens == 0:
        for message in messages:
            if hasattr(message, "content"):
                prompt_tokens += count_tokens(message.content)
            elif "content" in message:
                prompt_tokens += count_tokens(message["content"])

    if completion_tokens == 0:
        if streaming_message_responses is not None:
            for message in streaming_message_responses:
                completion_tokens += count_tokens(message)
        elif hasattr(response, "choices"):
            for choice in response.choices:
                if hasattr(choice, "message"):
                    completion_tokens += count_tokens(choice.message)

    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    if completion_tokens != 0:
        span.set_data(COMPLETION_TOKENS, completion_tokens)
    if prompt_tokens != 0:
        span.set_data(PROMPT_TOKENS, prompt_tokens)
    if total_tokens != 0:
        span.set_data(TOTAL_TOKENS, total_tokens)


def _wrap_chat_completion_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    @wraps(f)
    def new_chat_completion(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        hub = Hub.current
        integration = hub.get_integration(OpenAIIntegration)
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

        kwargs["messages"] = list(kwargs["messages"])
        messages = kwargs["messages"]
        model = kwargs.get("model")
        streaming = kwargs.get("stream")  # TODO handle streaming

        span = hub.start_span(op="openai", description="Chat Completion")
        span.__enter__()
        res = f(*args, **kwargs)
        with capture_internal_exceptions():
            span.set_data("messages", messages)
            span.set_tag("model", model)
            span.set_tag("streaming", streaming)

            if hasattr(res, "choices"):
                span.set_data("response", res.choices[0].message)
                _calculate_chat_completion_usage(messages, res, span)
                span.__exit__(None, None, None)
            elif hasattr(res, "_iterator"):
                data_buf: list[list[str]] = []  # one for each choice

                old_iterator: Iterator[ChatCompletionChunk] = res._iterator

                def new_iterator() -> Iterator[ChatCompletionChunk]:
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
                            all_responses = list(
                                map(lambda chunk: "".join(chunk), data_buf)
                            )
                            span.set_data("responses", all_responses)
                            _calculate_chat_completion_usage(
                                messages, res, span, all_responses
                            )
                    span.__exit__(None, None, None)

                res._iterator = new_iterator()
            else:
                span.set_tag("unknown_response", True)
                span.__exit__(None, None, None)
            return res

    return new_chat_completion


def _wrap_enbeddings_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_embeddings_create(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        hub = Hub.current
        integration = hub.get_integration(OpenAIIntegration)
        if integration is None:
            return f(*args, **kwargs)

        with hub.start_span(op="openai", description="Embeddings Creation") as span:
            if "input" in kwargs and isinstance(kwargs["input"], str):
                span.set_data("input", kwargs["input"])
            if "model" in kwargs:
                span.set_tag("model", kwargs["model"])
            if "dimensions" in kwargs:
                span.set_tag("dimensions", kwargs["dimensions"])
            response = f(*args, **kwargs)

            prompt_tokens = 0
            total_tokens = 0
            if hasattr(response, "usage"):
                if hasattr(response.usage, "prompt_tokens") and isinstance(
                    response.usage.prompt_tokens, int
                ):
                    prompt_tokens = response.usage.prompt_tokens
                if hasattr(response.usage, "total_tokens") and isinstance(
                    response.usage.total_tokens, int
                ):
                    total_tokens = response.usage.total_tokens

            if prompt_tokens == 0:
                prompt_tokens = count_tokens(kwargs["input"] or "")

            if total_tokens == 0:
                total_tokens = prompt_tokens

            span.set_data(PROMPT_TOKENS, prompt_tokens)
            span.set_data(TOTAL_TOKENS, total_tokens)

            return response

    return new_embeddings_create
