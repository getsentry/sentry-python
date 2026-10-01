import copy
import time
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME
from sentry_sdk.tracing_utils import (
    add_sentry_baggage_to_headers,
    should_propagate_trace,
)
from sentry_sdk.utils import capture_internal_exceptions, logger

from ..spans import ai_client_span, update_ai_client_span

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, Union

    from sentry_sdk.tracing import Span

try:
    import agents
    from agents.tool import HostedMCPTool
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _inject_trace_propagation_headers(
    hosted_tool: "HostedMCPTool", span: "Union[Span, StreamedSpan]"
) -> None:
    headers = hosted_tool.tool_config.get("headers")
    if headers is None:
        headers = {}
        hosted_tool.tool_config["headers"] = headers

    mcp_url = hosted_tool.tool_config.get("server_url")
    if not mcp_url:
        return

    if should_propagate_trace(sentry_sdk.get_client(), mcp_url):
        for (
            key,
            value,
        ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers(span=span):
            logger.debug(
                "[Tracing] Adding `{key}` header {value} to outgoing request to {mcp_url}.".format(
                    key=key, value=value, mcp_url=mcp_url
                )
            )
            if key == BAGGAGE_HEADER_NAME:
                add_sentry_baggage_to_headers(headers, value)
            else:
                headers[key] = value


class _ResponseModelRecordingStream:
    """
    Proxies a provider chunk stream returned by `_fetch_response(stream=True)` and records
    the model reported on each chunk.

    Streamed Chat Completions responses (also used by the LiteLLM model) are synthesized by
    the Agents SDK with `model` set to the requested model name, e.g. an Azure deployment
    name. Only the provider chunks carry the model that actually responded.
    """

    def __init__(self, stream: "Any", record: "Callable[[str], None]") -> None:
        self._sentry_stream = stream
        self._sentry_record = record

    def __getattr__(self, name: str) -> "Any":
        return getattr(self._sentry_stream, name)

    def __aiter__(self) -> "_ResponseModelRecordingStream":
        return self

    async def __anext__(self) -> "Any":
        chunk = await self._sentry_stream.__anext__()
        with capture_internal_exceptions():
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                self._sentry_record(str(chunk_model))
        return chunk


def _get_model(
    original_get_model: "Callable[..., agents.Model]",
    agent: "agents.Agent",
    run_config: "agents.RunConfig",
) -> "agents.Model":
    """
    Responsible for
    - creating and managing AI client spans.
    - adding trace propagation headers to tools with type HostedMCPTool.
    - setting the response model on agent invocation spans.
    """
    # copy the model to double patching its methods. We use copy on purpose here (instead of deepcopy)
    # because we only patch its direct methods, all underlying data can remain unchanged.
    model = copy.copy(original_get_model(agent, run_config))

    # The resolved model honors `RunConfig.model` and has provider prefixes such as "litellm/"
    # stripped, so it is the source of truth for the request model (agent.model can be None
    # when using defaults, or overridden by the run config).
    resolved_model_name = getattr(model, "model", None)
    request_model_name = (
        str(resolved_model_name) if resolved_model_name is not None else None
    )
    agent._sentry_request_model = request_model_name or str(model)  # type: ignore[attr-defined]

    # The model copy is created per turn, so this state is not shared between concurrent runs.
    # It holds the model reported by the provider for the in-flight request.
    response_model_state: "dict[str, Optional[str]]" = {"model": None}

    def _record_response_model(response_model: str) -> None:
        response_model_state["model"] = response_model

    def _pop_response_model() -> "Optional[str]":
        response_model = response_model_state["model"]
        response_model_state["model"] = None
        return response_model

    # Wrap _fetch_response if it exists (for OpenAI and LiteLLM models) to capture the response model
    if hasattr(model, "_fetch_response"):
        original_fetch_response = model._fetch_response

        @wraps(original_fetch_response)
        async def wrapped_fetch_response(*args: "Any", **kwargs: "Any") -> "Any":
            response = await original_fetch_response(*args, **kwargs)
            with capture_internal_exceptions():
                if hasattr(response, "model") and response.model:
                    _record_response_model(str(response.model))
                elif (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and hasattr(type(response[1]), "__anext__")
                ):
                    # Streamed Chat Completions return (synthesized Response, chunk stream).
                    return (
                        response[0],
                        _ResponseModelRecordingStream(
                            response[1], _record_response_model
                        ),
                    )
            return response

        model._fetch_response = wrapped_fetch_response

    original_get_response = model.get_response

    @wraps(original_get_response)
    async def wrapped_get_response(*args: "Any", **kwargs: "Any") -> "Any":
        mcp_tools = kwargs.get("tools")
        hosted_tools = []
        if mcp_tools is not None:
            hosted_tools = [
                tool for tool in mcp_tools if isinstance(tool, HostedMCPTool)
            ]

        _pop_response_model()
        with ai_client_span(agent, kwargs, request_model=request_model_name) as span:
            for hosted_tool in hosted_tools:
                _inject_trace_propagation_headers(hosted_tool, span=span)

            result = await original_get_response(*args, **kwargs)

            # Get response model captured from _fetch_response
            response_model = _pop_response_model()

            update_ai_client_span(span, result, response_model, agent)

        return result

    model.get_response = wrapped_get_response  # type: ignore[method-assign]

    # Also wrap stream_response for streaming support
    if hasattr(model, "stream_response"):
        original_stream_response = model.stream_response

        @wraps(original_stream_response)
        async def wrapped_stream_response(*args: "Any", **kwargs: "Any") -> "Any":
            span_kwargs = dict(kwargs)
            if len(args) > 0:
                span_kwargs["system_instructions"] = args[0]
            if len(args) > 1:
                span_kwargs["input"] = args[1]

            hosted_tools = []
            if len(args) > 3:
                mcp_tools = args[3]

                if mcp_tools is not None:
                    hosted_tools = [
                        tool for tool in mcp_tools if isinstance(tool, HostedMCPTool)
                    ]

            _pop_response_model()
            with ai_client_span(
                agent, span_kwargs, request_model=request_model_name
            ) as span:
                for hosted_tool in hosted_tools:
                    _inject_trace_propagation_headers(hosted_tool, span=span)

                set_on_span = (
                    span.set_attribute
                    if isinstance(span, StreamedSpan)
                    else span.set_data
                )
                set_on_span(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)

                streaming_response = None
                ttft_recorded = False
                # Capture start time locally to avoid race conditions with concurrent requests
                start_time = time.perf_counter()

                async for event in original_stream_response(*args, **kwargs):
                    # Detect first content token (text delta event)
                    if not ttft_recorded and hasattr(event, "delta"):
                        ttft = time.perf_counter() - start_time
                        set_on_span(SPANDATA.GEN_AI_RESPONSE_TIME_TO_FIRST_TOKEN, ttft)
                        ttft_recorded = True

                    # Capture the full response from ResponseCompletedEvent
                    if hasattr(event, "response"):
                        streaming_response = event.response
                    yield event

                # Update span with response data (usage, output, model)
                if streaming_response:
                    # Prefer the model reported by provider chunks: the terminal response of
                    # streamed Chat Completions only echoes the requested model name.
                    response_model = _pop_response_model() or (
                        str(streaming_response.model)
                        if hasattr(streaming_response, "model")
                        and streaming_response.model
                        else None
                    )
                    update_ai_client_span(
                        span, streaming_response, response_model, agent
                    )

        model.stream_response = wrapped_stream_response  # type: ignore[method-assign]

    return model
