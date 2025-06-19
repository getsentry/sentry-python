from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import ai_client_span, update_ai_client_span

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _create_get_model_wrapper(original_get_model):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """
    Wraps the agents.Runner._get_model method to wrap the get_response method of the model to create a AI client span.
    """

    @wraps(
        original_get_model.__func__
        if hasattr(original_get_model, "__func__")
        else original_get_model
    )
    def wrapped_get_model(cls, agent, run_config):
        # type: (agents.Runner, agents.Agent, agents.RunConfig) -> agents.Model

        model = original_get_model(agent, run_config)
        original_get_response = model.get_response

        @wraps(original_get_response)
        async def wrapped_get_response(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with ai_client_span(agent, kwargs) as span:
                result = await original_get_response(*args, **kwargs)

                update_ai_client_span(span, agent, kwargs, result)

            return result

        model.get_response = wrapped_get_response

        return model

    return wrapped_get_model
