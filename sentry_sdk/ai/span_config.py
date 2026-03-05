import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.ai.utils import (
    set_data_normalized,
    normalize_message_roles,
    truncate_and_annotate_messages,
)
from sentry_sdk.scope import should_send_default_pii

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict
    from sentry_sdk.tracing import Span


def set_input_span_data(span, kwargs, integration, config, span_data=None):
    # type: (Span, Dict[str, Any], Any, Dict[str, Any], Dict[str, Any] | None) -> None
    """
    Set input span data from a declarative config.

    Config keys:
        static: dict - key/value pairs to set unconditionally
        params: dict - kwargs key -> span attr (always set if present)
        pii_params: dict - kwargs key -> span attr (only when PII allowed)
        extract_messages: callable(kwargs) -> list or None
        message_target: str - span attr for messages (default: GEN_AI_REQUEST_MESSAGES)

    span_data: additional key/value pairs for dynamic per-call values
    """
    for key, value in config.get("static", {}).items():
        set_data_normalized(span, key, value)
    if span_data:
        for key, value in span_data.items():
            set_data_normalized(span, key, value)

    for kwarg_key, span_attr in config.get("params", {}).items():
        if kwarg_key in kwargs:
            value = kwargs[kwarg_key]
            set_data_normalized(span, span_attr, value)

    if should_send_default_pii() and integration.include_prompts:
        extract = config.get("extract_messages")
        if extract is not None:
            messages = extract(kwargs)
            if messages:
                messages = normalize_message_roles(messages)
                scope = sentry_sdk.get_current_scope()
                messages = truncate_and_annotate_messages(messages, span, scope)
                if messages is not None:
                    target = config.get(
                        "message_target", SPANDATA.GEN_AI_REQUEST_MESSAGES
                    )
                    set_data_normalized(span, target, messages, unpack=False)

        for kwarg_key, span_attr in config.get("pii_params", {}).items():
            if kwarg_key in kwargs:
                value = kwargs[kwarg_key]
                set_data_normalized(span, span_attr, value)
