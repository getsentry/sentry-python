import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.ai.utils import (
    get_first_from_sources,
    set_data_normalized,
    set_span_data_from_sources,
    normalize_message_roles,
    truncate_and_annotate_messages,
)
from sentry_sdk.scope import should_send_default_pii

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional
    from sentry_sdk.tracing import Span


def set_request_span_data(span, kwargs, integration, config, span_data=None):
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


def set_response_span_data(span, response, include_pii, response_config, collected_text=None):
    # type: (Span, Any, bool, Dict[str, Any], Optional[List[str]]) -> None
    """
    Set response span data from a declarative config.

    response_config keys:
        sources: dict - always set from response object
        pii_sources: dict - only when PII allowed
        extract_text: (response) -> list[str] | None (PII only)
        usage: dict with input_tokens/output_tokens source paths
    collected_text: pre-collected streaming text (overrides extract_text)
    """
    set_span_data_from_sources(
        span, response, response_config.get("sources", {}), require_truthy=False
    )

    if include_pii:
        pii_sources = response_config.get("pii_sources")
        if pii_sources:
            set_span_data_from_sources(
                span, response, pii_sources, require_truthy=True
            )
        if collected_text:
            set_data_normalized(
                span, SPANDATA.GEN_AI_RESPONSE_TEXT, ["".join(collected_text)]
            )
        else:
            extract_text = response_config.get("extract_text")
            if extract_text:
                texts = extract_text(response)
                if texts:
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, texts)

    usage_config = response_config.get("usage")
    if usage_config:
        record_token_usage(
            span,
            input_tokens=get_first_from_sources(
                response, usage_config.get("input_tokens", [])
            ),
            output_tokens=get_first_from_sources(
                response, usage_config.get("output_tokens", [])
            ),
        )
