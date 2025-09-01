from functools import wraps
from typing import Any, Callable, List, Optional

import sentry_sdk
from sentry_sdk.ai.monitoring import set_ai_pipeline_name
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import safe_serialize


try:
    from langgraph.graph import StateGraph
    from langgraph.pregel import Pregel
except ImportError:
    raise DidNotEnable("langgraph not installed")


class LanggraphIntegration(Integration):
    identifier = "langgraph"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True):
        # type: (LanggraphIntegration, bool) -> None
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None
        # LangGraph lets users create agents using a StateGraph or the Functional API.
        # StateGraphs are then compiled to a CompiledStateGraph. Both CompiledStateGraph and
        # the functional API execute on a Pregel instance. Pregel is the runtime for the graph
        # and the invocation happens on Pregel, so patching the invoke methods takes care of both.
        # The streaming methods are not patched, because due to some internal reasons, LangGraph
        # will automatically patch the streaming methods to run through invoke, and by doing this
        # we prevent duplicate spans for invocations.
        StateGraph.compile = _wrap_state_graph_compile(StateGraph.compile)
        if hasattr(Pregel, "invoke"):
            Pregel.invoke = _wrap_pregel_invoke(Pregel.invoke)
        if hasattr(Pregel, "ainvoke"):
            Pregel.ainvoke = _wrap_pregel_ainvoke(Pregel.ainvoke)


def _get_graph_name(graph_obj):
    # type: (Any) -> Optional[str]
    for attr in ["name", "graph_name", "__name__", "_name"]:
        if hasattr(graph_obj, attr):
            name = getattr(graph_obj, attr)
            if name and isinstance(name, str):
                return name
    return None


def _parse_langgraph_messages(state):
    # type: (Any) -> Optional[List[Any]]
    if not state:
        return None

    messages = None

    if isinstance(state, dict):
        messages = state.get("messages")
    elif hasattr(state, "messages"):
        messages = state.messages
    elif hasattr(state, "get") and callable(state.get):
        try:
            messages = state.get("messages")
        except Exception:
            pass

    if not messages or not isinstance(messages, (list, tuple)):
        return None

    normalized_messages = []
    for message in messages:
        try:
            if hasattr(message, "content"):
                parsed = {"content": message.content}
                for attr in ["name", "tool_calls", "function_call"]:
                    if hasattr(message, attr):
                        value = getattr(message, attr)
                        if value is not None:
                            parsed[attr] = value
                normalized_messages.append(parsed)

        except Exception:
            continue

    return normalized_messages if normalized_messages else None


def _wrap_state_graph_compile(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    @wraps(f)
    def new_compile(self, *args, **kwargs):
        integration = sentry_sdk.get_client().get_integration(LanggraphIntegration)

        compiled_graph = f(self, *args, **kwargs)
        if integration is None:
            return compiled_graph

        with sentry_sdk.start_span(
            op=OP.GEN_AI_CREATE_AGENT,
            name="create_agent",
            origin=LanggraphIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "create_agent")
            span.set_data(
                SPANDATA.GEN_AI_AGENT_NAME, getattr(compiled_graph, "name", None)
            )
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, kwargs.get("model"))
            tools = None
            graph = getattr(compiled_graph, "get_graph", None)
            if callable(graph):
                graph_obj = graph()
                nodes = getattr(graph_obj, "nodes", None)
                if nodes and isinstance(nodes, dict):
                    tools_node = nodes.get("tools")
                    if tools_node:
                        data = getattr(tools_node, "data", None)
                        if data and hasattr(data, "tools_by_name"):
                            tools = list(data.tools_by_name.keys())
            span.set_data(SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, tools)
            return compiled_graph

    return new_compile


def _wrap_pregel_invoke(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_invoke(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LanggraphIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        graph_name = _get_graph_name(self)

        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=(
                f"invoke_agent invoke {graph_name}".strip()
                if graph_name
                else "invoke_agent"
            ),
            origin=LanggraphIntegration.origin,
        ) as span:
            # Set agent metadata
            if graph_name:
                set_ai_pipeline_name(graph_name)
                span.set_data(SPANDATA.GEN_AI_PIPELINE_NAME, graph_name)
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, graph_name)

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, False)

            # Capture input messages if PII is allowed
            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                # import ipdb; ipdb.set_trace()
                parsed_messages = _parse_langgraph_messages(args[0])
                if parsed_messages:
                    span.set_data(
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        safe_serialize(parsed_messages),
                    )

            # Execute the graph
            try:
                result = f(self, *args, **kwargs)

                # Capture output state if PII is allowed
                if should_send_default_pii() and integration.include_prompts:
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, result)

                return result

            except Exception:
                span.set_status("internal_error")
                raise
            finally:
                if graph_name:
                    set_ai_pipeline_name(None)

    return new_invoke


def _wrap_pregel_ainvoke(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    async def new_ainvoke(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LanggraphIntegration)
        if integration is None:
            return await f(self, *args, **kwargs)

        graph_name = _get_graph_name(self)

        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name="invoke_agent",
            origin=LanggraphIntegration.origin,
        ) as span:
            if graph_name:
                set_ai_pipeline_name(graph_name)
                span.set_data(SPANDATA.GEN_AI_PIPELINE_NAME, graph_name)
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, graph_name)

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, False)

            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                parsed_messages = _parse_langgraph_messages(args[0])
                if parsed_messages:
                    span.set_data(
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        safe_serialize(parsed_messages),
                    )

            # Execute the graph
            try:
                result = await f(self, *args, **kwargs)

                # Capture output state if PII is allowed
                if should_send_default_pii() and integration.include_prompts:
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, result)

                return result

            except Exception:
                span.set_status("internal_error")
                raise
            finally:
                if graph_name:
                    set_ai_pipeline_name(None)

    new_ainvoke.__wrapped__ = True
    return new_ainvoke
