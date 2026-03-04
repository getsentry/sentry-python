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


def set_input_span_data(span, kwargs, integration, config):
    # type: (Span, Dict[str, Any], Any, Dict[str, Any]) -> None
    """
    Set input span data from a declarative config.

    Config keys:
        system: str - gen_ai.system value
        operation: str - gen_ai.operation.name value
        params: dict - kwargs key -> span attr (always set if present)
        pii_params: dict - kwargs key -> span attr (only when PII allowed)
        extract_messages: callable(kwargs) -> list or None
        message_target: str - span attr for messages (default: GEN_AI_REQUEST_MESSAGES)
        truncation_fn: callable or None - truncation function (default: truncate_and_annotate_messages, None to skip)
        is_given: callable(value) -> bool - for NotGiven sentinels
        extra_static: dict - additional key/value pairs to set
    """
    set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, config["system"])
    set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, config["operation"])

    is_given = config.get("is_given")
    for kwarg_key, span_attr in config.get("params", {}).items():
        if kwarg_key in kwargs:
            value = kwargs[kwarg_key]
            if is_given is None or is_given(value):
                set_data_normalized(span, span_attr, value)

    if should_send_default_pii() and integration.include_prompts:
        extract = config.get("extract_messages")
        if extract is not None:
            messages = extract(kwargs)
            if messages:
                messages = normalize_message_roles(messages)
                truncation_fn = config.get(
                    "truncation_fn", truncate_and_annotate_messages
                )
                if truncation_fn is not None:
                    scope = sentry_sdk.get_current_scope()
                    messages = truncation_fn(messages, span, scope)
                if messages is not None:
                    target = config.get(
                        "message_target", SPANDATA.GEN_AI_REQUEST_MESSAGES
                    )
                    set_data_normalized(span, target, messages, unpack=False)

        for kwarg_key, span_attr in config.get("pii_params", {}).items():
            if kwarg_key in kwargs:
                value = kwargs[kwarg_key]
                if is_given is None or is_given(value):
                    set_data_normalized(span, span_attr, value)

    for key, value in config.get("extra_static", {}).items():
        set_data_normalized(span, key, value)
