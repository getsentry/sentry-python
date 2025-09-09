from functools import wraps

from typing import Any, Iterable, Callable

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
)

try:
    import huggingface_hub.inference._client

    from huggingface_hub import (
        ChatCompletionOutput,
        TextGenerationOutput,
        ChatCompletionStreamOutput,
    )
except ImportError:
    raise DidNotEnable("Huggingface not installed")


class HuggingfaceHubIntegration(Integration):
    identifier = "huggingface_hub"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True):
        # type: (HuggingfaceHubIntegration, bool) -> None
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None

        # Other tasks that can be called: https://huggingface.co/docs/huggingface_hub/guides/inference#supported-providers-and-tasks
        huggingface_hub.inference._client.InferenceClient.text_generation = (
            _wrap_huggingface_task(
                huggingface_hub.inference._client.InferenceClient.text_generation,
                OP.GEN_AI_GENERATE_TEXT,
            )
        )
        huggingface_hub.inference._client.InferenceClient.chat_completion = (
            _wrap_huggingface_task(
                huggingface_hub.inference._client.InferenceClient.chat_completion,
                OP.GEN_AI_CHAT,
            )
        )


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "huggingface_hub", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _wrap_huggingface_task(f, op):
    # type: (Callable[..., Any], str) -> Callable[..., Any]
    @wraps(f)
    def new_huggingface_task(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(HuggingfaceHubIntegration)
        if integration is None:
            return f(*args, **kwargs)

        if "prompt" in kwargs:
            prompt = kwargs["prompt"]
        elif "messages" in kwargs:
            prompt = kwargs["messages"]
        elif len(args) >= 2:
            kwargs["prompt"] = args[1]
            prompt = kwargs["prompt"]
            args = (args[0],) + args[2:]
        else:
            # invalid call, dont instrument, let it return error
            return f(*args, **kwargs)

        client = args[0]
        model = client.model or kwargs.get("model") or ""
        operation_name = op.split(".")[-1]

        span = sentry_sdk.start_span(
            op=op,
            name=f"{operation_name} {model}",
            origin=HuggingfaceHubIntegration.origin,
        )
        span.__enter__()

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, operation_name)

        if model:
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)

        # Input attributes
        attribute_mapping = {
            "tools": SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
            "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
            "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
            "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
            "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
            "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
            "top_k": SPANDATA.GEN_AI_REQUEST_TOP_K,
            "stream": SPANDATA.GEN_AI_RESPONSE_STREAMING,
        }
        for attribute, span_attribute in attribute_mapping.items():
            value = kwargs.get(attribute, None)
            if value is not None:
                if isinstance(value, (int, float, bool, str)):
                    span.set_data(span_attribute, value)
                else:
                    set_data_normalized(span, span_attribute, value, unpack=False)

        try:
            res = f(*args, **kwargs)
        except Exception as e:
            span.set_status("error")
            _capture_exception(e)
            span.__exit__(None, None, None)
            raise e from None

        with capture_internal_exceptions():
            # Output attributes
            if hasattr(res, "model"):
                model = res.model
                if model:
                    span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, model)

            finish_reason = None
            if hasattr(res, "details") and res.details is not None:
                finish_reason = getattr(res.details, "finish_reason", None)

            if finish_reason is None:
                try:
                    finish_reason = res.choices[0].finish_reason
                except Exception:
                    pass

            if finish_reason:
                span.set_data(SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reason)

            if should_send_default_pii() and integration.include_prompts:
                set_data_normalized(
                    span, SPANDATA.GEN_AI_REQUEST_MESSAGES, prompt, unpack=False
                )

                try:
                    tool_calls = res.choices[0].message.tool_calls
                except Exception:
                    tool_calls = []

                if tool_calls is not None and len(tool_calls) > 0:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                        tool_calls,
                        unpack=False,
                    )

            if isinstance(res, str):
                if should_send_default_pii() and integration.include_prompts:
                    if res:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_RESPONSE_TEXT,
                            res,
                        )
                span.__exit__(None, None, None)
                return res

            if isinstance(res, TextGenerationOutput):
                if should_send_default_pii() and integration.include_prompts:
                    if res.generated_text:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_RESPONSE_TEXT,
                            res.generated_text,
                        )
                if res.details is not None and res.details.generated_tokens > 0:
                    record_token_usage(
                        span,
                        total_tokens=res.details.generated_tokens,
                    )
                span.__exit__(None, None, None)
                return res

            if isinstance(res, ChatCompletionOutput):
                if should_send_default_pii() and integration.include_prompts:
                    text_response = "".join(
                        [
                            x.get("message", {}).get("content", None) or ""
                            for x in res.choices
                        ]
                    )
                    if text_response:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_RESPONSE_TEXT,
                            text_response,
                        )
                if hasattr(res, "usage") and res.usage is not None:
                    record_token_usage(
                        span,
                        input_tokens=res.usage.prompt_tokens,
                        output_tokens=res.usage.completion_tokens,
                        total_tokens=res.usage.total_tokens,
                    )
                span.__exit__(None, None, None)
                return res

            if not isinstance(res, Iterable):
                span.__exit__(None, None, None)
                return res

            if kwargs.get("details", False):
                # text-generation stream output
                def new_details_iterator():
                    # type: () -> Iterable[Any]
                    with capture_internal_exceptions():
                        tokens_used = 0
                        data_buf: list[str] = []
                        for chunk in res:
                            if hasattr(chunk, "token") and hasattr(chunk.token, "text"):
                                data_buf.append(chunk.token.text)
                            if hasattr(chunk, "details") and hasattr(
                                chunk.details, "generated_tokens"
                            ):
                                tokens_used = chunk.details.generated_tokens
                            if hasattr(chunk, "details") and hasattr(
                                chunk.details, "finish_reason"
                            ):
                                span.set_data(
                                    SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
                                    chunk.details.finish_reason,
                                )

                            yield chunk

                        if (
                            len(data_buf) > 0
                            and should_send_default_pii()
                            and integration.include_prompts
                        ):
                            text_response = "".join(data_buf)
                            if text_response:
                                set_data_normalized(
                                    span,
                                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                                    text_response,
                                )
                        if tokens_used > 0:
                            record_token_usage(
                                span,
                                total_tokens=tokens_used,
                            )

                    span.__exit__(None, None, None)

                return new_details_iterator()
            else:
                # chat-completion stream output
                def new_iterator():
                    # type: () -> Iterable[str]
                    with capture_internal_exceptions():
                        data_buf: list[str] = []
                        for chunk in res:
                            if isinstance(chunk, ChatCompletionStreamOutput):
                                for choice in chunk.choices:
                                    if (
                                        hasattr(choice, "delta")
                                        and hasattr(choice.delta, "content")
                                        and choice.delta.content is not None
                                    ):
                                        data_buf.append(choice.delta.content)

                                    if (
                                        hasattr(choice, "finish_reason")
                                        and choice.finish_reason is not None
                                    ):
                                        span.set_data(
                                            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS,
                                            choice.finish_reason,
                                        )
                                    if (
                                        hasattr(choice, "delta")
                                        and hasattr(choice.delta, "tool_calls")
                                        and choice.delta.tool_calls is not None
                                    ):
                                        if (
                                            should_send_default_pii()
                                            and integration.include_prompts
                                        ):
                                            set_data_normalized(
                                                span,
                                                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                                                choice.delta.tool_calls,
                                                unpack=False,
                                            )

                                if hasattr(chunk, "model") and chunk.model is not None:
                                    span.set_data(
                                        SPANDATA.GEN_AI_RESPONSE_MODEL, chunk.model
                                    )

                                if hasattr(chunk, "usage") and chunk.usage is not None:
                                    record_token_usage(
                                        span,
                                        input_tokens=chunk.usage.prompt_tokens,
                                        output_tokens=chunk.usage.completion_tokens,
                                        total_tokens=chunk.usage.total_tokens,
                                    )

                            elif isinstance(chunk, str):
                                if chunk is not None:
                                    data_buf.append(chunk)

                            yield chunk

                        if (
                            len(data_buf) > 0
                            and should_send_default_pii()
                            and integration.include_prompts
                        ):
                            text_response = "".join(data_buf)
                            if text_response:
                                set_data_normalized(
                                    span,
                                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                                    text_response,
                                )

                        span.__exit__(None, None, None)

                return new_iterator()

    return new_huggingface_task
