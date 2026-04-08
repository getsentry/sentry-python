from functools import wraps
from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.consts import SPANDATA

try:
    from pydantic_ai import models  # type: ignore
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")

from ..spans import ai_client_span, update_ai_client_span

from ..utils import (
    _set_agent_data,
    _set_available_tools,
    get_current_agent,
    get_is_streaming,
)

if TYPE_CHECKING:
    from typing import Any


def _patch_model_request() -> None:
    """
    Patches model request execution to create AI client spans.

    In pydantic-ai, model requests are handled through the Model interface.
    We need to patch the request method on models to create spans.
    """

    # Patch the base Model class's request method
    if hasattr(models, "Model"):
        original_request = models.Model.request

        @wraps(original_request)
        async def wrapped_request(
            self: "Any", messages: "Any", *args: "Any", **kwargs: "Any"
        ) -> "Any":
            # Pass all messages (full conversation history)
            with ai_client_span(messages, self, None) as span:
                _set_agent_data(span, None)
                # Set streaming flag from contextvar
                span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, get_is_streaming())

                # Add available tools if agent is available
                agent = get_current_agent()
                _set_available_tools(span, agent)

                result = await original_request(self, messages, *args, **kwargs)
                update_ai_client_span(span, result)
                return result

        models.Model.request = wrapped_request
