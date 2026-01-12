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

        # Also wrap stream_response for streaming support
        if hasattr(model, "stream_response"):
            original_stream_response = model.stream_response

            @wraps(original_stream_response)
            async def wrapped_stream_response(*args: "Any", **kwargs: "Any") -> "Any":
                """
                Wrap stream_response to create an AI client span for streaming.
                stream_response is an async generator, so we yield events within the span.

                Note: stream_response is called with positional args unlike get_response
                which uses keyword args. The signature is:
                    stream_response(
                        system_instructions,  # args[0]
                        input,                # args[1]
                        model_settings,       # args[2]
                        tools,                # args[3]
                        output_schema,        # args[4]
                        handoffs,             # args[5]
                        tracing,              # args[6]
                        *,
                        previous_response_id,
                        conversation_id,
                        prompt,
                    )
                """
                # Build kwargs dict from positional args for span data capture
                span_kwargs = dict(kwargs)
                if len(args) > 0:
                    span_kwargs["system_instructions"] = args[0]
                if len(args) > 1:
                    span_kwargs["input"] = args[1]

                with ai_client_span(agent, span_kwargs) as span:
                    span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)

                    async for event in original_stream_response(*args, **kwargs):
                        yield event

                    # Get response model if captured
                    response_model = getattr(agent, "_sentry_raw_response_model", None)
                    if response_model:
                        agent_span = getattr(agent, "_sentry_agent_span", None)
                        if agent_span:
                            agent_span.set_data(
                                SPANDATA.GEN_AI_RESPONSE_MODEL, response_model
                            )
                        delattr(agent, "_sentry_raw_response_model")

            model.stream_response = wrapped_stream_response

        return model

    return wrapped_get_model
