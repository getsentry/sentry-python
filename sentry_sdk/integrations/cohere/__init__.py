import sys
from functools import wraps

from sentry_sdk.ai.monitoring import record_token_usage
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.ai.utils import set_data_normalized

from typing import TYPE_CHECKING

from sentry_sdk.tracing_utils import set_span_errored

if TYPE_CHECKING:
    from typing import Any, Callable

import sentry_sdk
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception, reraise

try:
    from cohere import __version__ as cohere_version  # noqa: F401
except ImportError:
    raise DidNotEnable("Cohere not installed")

COLLECTED_CHAT_PARAMS = {
    "model": SPANDATA.GEN_AI_REQUEST_MODEL,
    "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
    "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
    "k": SPANDATA.GEN_AI_REQUEST_TOP_K,
    "p": SPANDATA.GEN_AI_REQUEST_TOP_P,
    "seed": SPANDATA.GEN_AI_REQUEST_SEED,
    "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
}


class CohereIntegration(Integration):
    identifier = "cohere"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True):
        # type: (bool) -> None
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None
        # Lazy imports to avoid circular dependencies:
        # v1/v2 import COLLECTED_CHAT_PARAMS and _capture_exception from this module.
        from sentry_sdk.integrations.cohere.v1 import setup_v1
        from sentry_sdk.integrations.cohere.v2 import setup_v2

        setup_v1(_wrap_embed)
        setup_v2(_wrap_embed)


def _capture_exception(exc):
    # type: (Any) -> None
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "cohere", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _wrap_embed(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    @wraps(f)
    def new_embed(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if integration is None:
            return f(*args, **kwargs)

        model = kwargs.get("model", "")

        with sentry_sdk.start_span(
            op=OP.GEN_AI_EMBEDDINGS,
            name=f"embeddings {model}".strip(),
            origin=CohereIntegration.origin,
        ) as span:
            set_data_normalized(span, SPANDATA.GEN_AI_SYSTEM, "cohere")
            set_data_normalized(span, SPANDATA.GEN_AI_OPERATION_NAME, "embeddings")

            if "texts" in kwargs and (
                should_send_default_pii() and integration.include_prompts
            ):
                if isinstance(kwargs["texts"], str):
                    set_data_normalized(
                        span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, [kwargs["texts"]]
                    )
                elif (
                    isinstance(kwargs["texts"], list)
                    and len(kwargs["texts"]) > 0
                    and isinstance(kwargs["texts"][0], str)
                ):
                    set_data_normalized(
                        span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, kwargs["texts"]
                    )

            if "model" in kwargs:
                set_data_normalized(
                    span, SPANDATA.GEN_AI_REQUEST_MODEL, kwargs["model"]
                )
            try:
                res = f(*args, **kwargs)
            except Exception as e:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    _capture_exception(e)
                reraise(*exc_info)
            if (
                hasattr(res, "meta")
                and hasattr(res.meta, "billed_units")
                and hasattr(res.meta.billed_units, "input_tokens")
            ):
                record_token_usage(
                    span,
                    input_tokens=res.meta.billed_units.input_tokens,
                    total_tokens=res.meta.billed_units.input_tokens,
                )
            return res

    return new_embed
