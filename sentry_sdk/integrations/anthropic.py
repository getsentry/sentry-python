from sentry_sdk.consts import OP
from sentry_sdk._functools import wraps
from sentry_sdk.integrations import DidNotEnable, Integration
import sentry_sdk
from sentry_sdk.utils import (
    package_version,
    event_from_exception,
    capture_internal_exceptions,
)
from sentry_sdk.hub import Hub, _should_send_default_pii
from typing import TYPE_CHECKING

from anthropic.resources import Messages

if TYPE_CHECKING:
    from typing import Any, Iterator
    from anthropic.types import MessageStreamEvent
    from sentry_sdk.tracing import Span

COMPLETION_TOKENS_USED = "ai.completion_tоkens.used"
PROMPT_TOKENS_USED = "ai.prompt_tоkens.used"
TOTAL_TOKENS_USED = "ai.total_tоkens.used"


def _capture_exception(hub, exc):
    # type: (Hub, Any) -> None

    if hub.client is not None:
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options,
            mechanism={"type": "anthropic", "handled": False},
        )
        hub.capture_event(event, hint=hint)


class AnthropicIntegration(Integration):
    identifier = "anthropic"

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

    if total_tokens != 0:
        span.set_data(TOTAL_TOKENS_USED, total_tokens)
    if input_tokens != 0:
        span.set_data(PROMPT_TOKENS_USED, input_tokens)
    if output_tokens != 0:
        span.set_data(COMPLETION_TOKENS_USED, output_tokens)


def _wrap_message_create(f):
    @wraps(f)
    def _sentry_patched_create(*args, **kwargs):
        hub = Hub.current
        if not hub:
            return f(*args, **kwargs)

        integration = hub.get_integration(
            AnthropicIntegration
        )  # type: AnthropicIntegration
        if not integration:
            return f(*args, **kwargs)

        if "messages" not in kwargs:
            return f(*args, **kwargs)

        try:
            iter(kwargs["messages"])
        except TypeError:
            return f(*args, **kwargs)

        messages = list(kwargs["messages"])
        model = kwargs.get("model")

        span = sentry_sdk.start_span(
            op=OP.ANTHROPIC_MESSAGES_CREATE, description="Anthropic messages create"
        )

        try:
            result = f(*args, **kwargs)
        except Exception as exc:
            _capture_exception(hub, exc)
            span.finish()
            raise exc from None

        with capture_internal_exceptions():
            span.set_data("ai.model_id", model)
            span.set_data("ai.streaming", False)
            if _should_send_default_pii() and integration.include_prompts:
                span.set_data("ai.input_messages", messages)
            if hasattr(result, "content"):
                if _should_send_default_pii() and integration.include_prompts:
                    span.set_data(
                        "ai.responses",
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
                span.finish()
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

                        if _should_send_default_pii() and integration.include_prompts:
                            complete_message = "".join(content_blocks)
                            span.set_data(
                                "ai.responses",
                                [{"type": "text", "text": complete_message}],
                            )
                        span.set_data(TOTAL_TOKENS_USED, input_tokens + output_tokens)
                        span.set_data(PROMPT_TOKENS_USED, input_tokens)
                        span.set_data(COMPLETION_TOKENS_USED, output_tokens)
                        span.set_data("ai.streaming", True)
                    span.finish()

                result._iterator = new_iterator()
            else:
                span.set_data("unknown_response", True)
                span.finish()

        return result

    return _sentry_patched_create
