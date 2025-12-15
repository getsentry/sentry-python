import copy
from functools import wraps

from sentry_sdk.integrations import DidNotEnable

from ..spans import ai_client_span, update_ai_client_span
from sentry_sdk.consts import SPANDATA

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable


try:
    import agents
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _create_get_model_wrapper(
    original_get_model: "Callable[..., Any]",
) -> "Callable[..., Any]":
    """
    Wraps the agents.Runner._get_model method to wrap the get_response method of the model to create a AI client span.
    """

    @wraps(
        original_get_model.__func__
        if hasattr(original_get_model, "__func__")
        else original_get_model
    )
    def wrapped_get_model(
        cls: "agents.Runner", agent: "agents.Agent", run_config: "agents.RunConfig"
    ) -> "agents.Model":
        # copy the model to double patching its methods. We use copy on purpose here (instead of deepcopy)
        # because we only patch its direct methods, all underlying data can remain unchanged.
        model = copy.copy(original_get_model(agent, run_config))

        # Wrap _fetch_response if it exists (for OpenAI models) to capture raw response model
        if hasattr(model, "_fetch_response"):
            original_fetch_response = model._fetch_response

            @wraps(original_fetch_response)
            async def wrapped_fetch_response(*args: "Any", **kwargs: "Any") -> "Any":
                response = await original_fetch_response(*args, **kwargs)
                if hasattr(response, "model"):
                    agent._sentry_raw_response_model = str(response.model)
                return response

            model._fetch_response = wrapped_fetch_response

        original_get_response = model.get_response

        @wraps(original_get_response)
        async def wrapped_get_response(*args: "Any", **kwargs: "Any") -> "Any":
            with ai_client_span(agent, kwargs) as span:
                result = await original_get_response(*args, **kwargs)

                response_model = getattr(agent, "_sentry_raw_response_model", None)
                if response_model:
                    agent_span = getattr(agent, "_sentry_agent_span", None)
                    if agent_span:
                        agent_span.set_data(
                            SPANDATA.GEN_AI_RESPONSE_MODEL, response_model
                        )

                    delattr(agent, "_sentry_raw_response_model")

                update_ai_client_span(span, agent, kwargs, result, response_model)

            return result

        model.get_response = wrapped_get_response

        return model

    return wrapped_get_model
