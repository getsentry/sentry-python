from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import ai_client_span, update_ai_client_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable

try:
    import pydantic_ai
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


def _patch_model_request():
    # type: () -> None
    """
    Patches model request execution to create AI client spans.

    In pydantic-ai, model requests are handled through the Model interface.
    We need to patch the request method on models to create spans.
    """
    from pydantic_ai import models

    # Patch the base Model class's request method
    if hasattr(models, "Model"):
        original_request = models.Model.request

        @wraps(original_request)
        async def wrapped_request(self, messages, *args, **kwargs):
            # type: (Any, Any, *Any, **Any) -> Any
            # Extract the first message to use as the request
            model_request = messages[0] if messages else None

            with ai_client_span(model_request, None, self, None) as span:
                result = await original_request(self, messages, *args, **kwargs)
                update_ai_client_span(span, result)
                return result

        models.Model.request = wrapped_request  # type: ignore
