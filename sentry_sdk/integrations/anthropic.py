from functools import wraps

import sentry_sdk
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    package_version,
)

from typing import TYPE_CHECKING

try:
    from anthropic.resources import Messages

    if TYPE_CHECKING:
        from anthropic.types import MessageStreamEvent
except ImportError:
    raise DidNotEnable("Anthropic not installed")


if TYPE_CHECKING:
    from typing import Any, Iterator
    from sentry_sdk.tracing import Span


class AnthropicIntegration(Integration):
    identifier = "anthropic"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True):
        # type: (AnthropicIntegration, bool) -> None
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None
        version = package_version("anthropic")

        if version is None:
            raise DidNotEnable("Unparsable anthropic version.")

        if version < (0, 16):
            raise DidNotEnable("anthropic 0.16 or newer required.")

        Messages.create = _wrap_message_create(Messages.create)


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "anthropic", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _calculate_token_usage(result, span):
    # type: (Messages, Span) -> None
    input_tokens = 0
    output_tokens = 0
    if hasattr(result, "usage"):
        usage = result.usage
        if hasattr(usage, "input_tokens") and isinstance(usage.input_tokens, int):
            input_tokens = usage.input_tokens
        if hasattr(usage, "output_tokens") and isinstance(usage.output_tokens, int):
            output_tokens = usage.output_tokens

    total_tokens = input_tokens + output_tokens
    record_token_usage(span, input_tokens, output_tokens, total_tokens)


def _wrap_message_create(f):
    # type: (Any) -> Any
    @wraps(f)
    def _sentry_patched_create(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(AnthropicIntegration)

        if integration is None or "messages" not in kwargs:
            return f(*args, **kwargs)

        try:
            iter(kwargs["messages"])
        except TypeError:
            return f(*args, **kwargs)

        messages = list(kwargs["messages"])
        model = kwargs.get("model")

        span = sentry_sdk.start_span(
            op=OP.ANTHROPIC_MESSAGES_CREATE,
            name="Anthropic messages create",
            origin=AnthropicIntegration.origin,
        )
        span.__enter__()

        try:
            result = f(*args, **kwargs)
        except Exception as exc:
            _capture_exception(exc)
            span.__exit__(None, None, None)
            raise exc from None

        with capture_internal_exceptions():
            span.set_data(SPANDATA.AI_MODEL_ID, model)
            span.set_data(SPANDATA.AI_STREAMING, False)
            if should_send_default_pii() and integration.include_prompts:
                span.set_data(SPANDATA.AI_INPUT_MESSAGES, messages)
            if hasattr(result, "content"):
                if should_send_default_pii() and integration.include_prompts:
                    span.set_data(
                        SPANDATA.AI_RESPONSES,
                        list(
                            map(
                                lambda message: {
                                    "type": message.type,
                                    "text": message.text,
                                },
                                result.content,
                            )
                        ),
                    )
                _calculate_token_usage(result, span)
                span.__exit__(None, None, None)
            elif hasattr(result, "_iterator"):
                old_iterator = result._iterator

                def new_iterator():
                    # type: () -> Iterator[MessageStreamEvent]
                    input_tokens = 0
                    output_tokens = 0
                    content_blocks = []
                    with capture_internal_exceptions():
                        for event in old_iterator:
                            if hasattr(event, "type"):
                                if event.type == "message_start":
                                    usage = event.message.usage
                                    input_tokens += usage.input_tokens
                                    output_tokens += usage.output_tokens
                                elif event.type == "content_block_start":
                                    pass
                                elif event.type == "content_block_delta":
                                    content_blocks.append(event.delta.text)
                                elif event.type == "content_block_stop":
                                    pass
                                elif event.type == "message_delta":
                                    output_tokens += event.usage.output_tokens
                                elif event.type == "message_stop":
                                    continue
                            yield event

                        if should_send_default_pii() and integration.include_prompts:
                            complete_message = "".join(content_blocks)
                            span.set_data(
                                SPANDATA.AI_RESPONSES,
                                [{"type": "text", "text": complete_message}],
                            )
                        total_tokens = input_tokens + output_tokens
                        record_token_usage(
                            span, input_tokens, output_tokens, total_tokens
                        )
                        span.set_data(SPANDATA.AI_STREAMING, True)
                    span.__exit__(None, None, None)

                result._iterator = new_iterator()
            else:
                span.set_data("unknown_response", True)
                span.__exit__(None, None, None)

        return result

    return _sentry_patched_create
