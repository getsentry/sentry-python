from functools import wraps

from sentry_sdk import consts
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.consts import SPANDATA
from sentry_sdk.ai.utils import set_data_normalized

if TYPE_CHECKING:
    from typing import Any, Iterable, List, Optional, Callable, Iterator
    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    ensure_integration_enabled,
)

try:
    from openai.resources.chat.completions import Completions
    from openai.resources import Embeddings

    if TYPE_CHECKING:
        from openai.types.chat import ChatCompletionMessageParam, ChatCompletionChunk
except ImportError:
    raise DidNotEnable("OpenAI not installed")


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
        Embeddings.create = _wrap_embeddings_create(Embeddings.create)

    def count_tokens(self, s):
        # type: (OpenAIIntegration, str) -> int
        if self.tiktoken_encoding is not None:
            return len(self.tiktoken_encoding.encode_ordinary(s))
        return 0


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _calculate_chat_completion_usage(
    messages, response, span, streaming_message_responses, count_tokens
):
    # type: (Iterable[ChatCompletionMessageParam], Any, Span, Optional[List[str]], Callable[..., Any]) -> None
    completion_tokens = 0  # type: Optional[int]
    prompt_tokens = 0  # type: Optional[int]
    total_tokens = 0  # type: Optional[int]
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
            if "content" in message:
                prompt_tokens += count_tokens(message["content"])

    if completion_tokens == 0:
        if streaming_message_responses is not None:
            for message in streaming_message_responses:
                completion_tokens += count_tokens(message)
        elif hasattr(response, "choices"):
            for choice in response.choices:
                if hasattr(choice, "message"):
                    completion_tokens += count_tokens(choice.message)

    if prompt_tokens == 0:
        prompt_tokens = None
    if completion_tokens == 0:
        completion_tokens = None
    if total_tokens == 0:
        total_tokens = None
    record_token_usage(span, prompt_tokens, completion_tokens, total_tokens)


def _wrap_chat_completion_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @ensure_integration_enabled(OpenAIIntegration, f)
    def new_chat_completion(*args, **kwargs):
        # type: (*Any, **Any) -> Any
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
        streaming = kwargs.get("stream")

        span = sentry_sdk.start_span(
            op=consts.OP.OPENAI_CHAT_COMPLETIONS_CREATE,
            description="Chat Completion",
            origin=OpenAIIntegration.origin,
        )
        span.__enter__()
        try:
            res = f(*args, **kwargs)
        except Exception as e:
            _capture_exception(e)
            span.__exit__(None, None, None)
            raise e from None

        integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)

        with capture_internal_exceptions():
            if should_send_default_pii() and integration.include_prompts:
                set_data_normalized(span, SPANDATA.AI_INPUT_MESSAGES, messages)

            set_data_normalized(span, SPANDATA.AI_MODEL_ID, model)
            set_data_normalized(span, SPANDATA.AI_STREAMING, streaming)

            if hasattr(res, "choices"):
                if should_send_default_pii() and integration.include_prompts:
                    set_data_normalized(
                        span,
                        "ai.responses",
                        list(map(lambda x: x.message, res.choices)),
                    )
                _calculate_chat_completion_usage(
                    messages, res, span, None, integration.count_tokens
                )
                span.__exit__(None, None, None)
            elif hasattr(res, "_iterator"):
                data_buf: list[list[str]] = []  # one for each choice

                old_iterator = res._iterator  # type: Iterator[ChatCompletionChunk]

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
                            all_responses = list(
                                map(lambda chunk: "".join(chunk), data_buf)
                            )
                            if (
                                should_send_default_pii()
                                and integration.include_prompts
                            ):
                                set_data_normalized(
                                    span, SPANDATA.AI_RESPONSES, all_responses
                                )
                            _calculate_chat_completion_usage(
                                messages,
                                res,
                                span,
                                all_responses,
                                integration.count_tokens,
                            )
                    span.__exit__(None, None, None)

                res._iterator = new_iterator()
            else:
                set_data_normalized(span, "unknown_response", True)
                span.__exit__(None, None, None)
        return res

    return new_chat_completion


def _wrap_embeddings_create(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    @ensure_integration_enabled(OpenAIIntegration, f)
    def new_embeddings_create(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        with sentry_sdk.start_span(
            op=consts.OP.OPENAI_EMBEDDINGS_CREATE,
            description="OpenAI Embedding Creation",
            origin=OpenAIIntegration.origin,
        ) as span:
            integration = sentry_sdk.get_client().get_integration(OpenAIIntegration)
            if "input" in kwargs and (
                should_send_default_pii() and integration.include_prompts
            ):
                if isinstance(kwargs["input"], str):
                    set_data_normalized(span, "ai.input_messages", [kwargs["input"]])
                elif (
                    isinstance(kwargs["input"], list)
                    and len(kwargs["input"]) > 0
                    and isinstance(kwargs["input"][0], str)
                ):
                    set_data_normalized(span, "ai.input_messages", kwargs["input"])
            if "model" in kwargs:
                set_data_normalized(span, "ai.model_id", kwargs["model"])
            try:
                response = f(*args, **kwargs)
            except Exception as e:
                _capture_exception(e)
                raise e from None

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
                prompt_tokens = integration.count_tokens(kwargs["input"] or "")

            record_token_usage(span, prompt_tokens, None, total_tokens or prompt_tokens)

            return response

    return new_embeddings_create
