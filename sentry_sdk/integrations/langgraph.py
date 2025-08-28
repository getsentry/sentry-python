from functools import wraps
from typing import Any, Callable, List, Optional

import sentry_sdk
from sentry_sdk.ai.monitoring import set_ai_pipeline_name
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii


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
        # Wrap StateGraph methods - these get called when the graph is compiled
        StateGraph.compile = _wrap_state_graph_compile(StateGraph.compile)

        # Wrap Pregel methods - these are the actual execution methods on compiled graphs
        if hasattr(Pregel, "invoke"):
            Pregel.invoke = _wrap_pregel_invoke(Pregel.invoke)
        if hasattr(Pregel, "ainvoke"):
            Pregel.ainvoke = _wrap_pregel_ainvoke(Pregel.ainvoke)


def _get_graph_name(graph_obj):
    # type: (Any) -> Optional[str]
    """Extract graph name from various possible attributes."""
    # Check for Sentry-specific agent name first
    if hasattr(graph_obj, "_sentry_agent_name"):
        return graph_obj._sentry_agent_name

    # Try to get name from different possible attributes
    for attr in ["name", "graph_name", "__name__", "_name"]:
        if hasattr(graph_obj, attr):
            name = getattr(graph_obj, attr)
            if name and isinstance(name, str):
                return name
    return None


def _get_graph_metadata(graph_obj):
    # type: (Any) -> tuple[Optional[str], Optional[List[str]]]
    """Extract graph name and node names if available."""
    graph_name = _get_graph_name(graph_obj)

    # Try to get node names from the graph
    node_names = None
    if hasattr(graph_obj, "nodes"):
        try:
            nodes = graph_obj.nodes
            if isinstance(nodes, dict):
                node_names = list(nodes.keys())
            elif hasattr(nodes, "__iter__"):
                node_names = list(nodes)
        except Exception:
            pass
    elif hasattr(graph_obj, "graph") and hasattr(graph_obj.graph, "nodes"):
        try:
            nodes = graph_obj.graph.nodes
            if isinstance(nodes, dict):
                node_names = list(nodes.keys())
            elif hasattr(nodes, "__iter__"):
                node_names = list(nodes)
        except Exception:
            pass

    return graph_name, node_names


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
            # import ipdb; ipdb.set_trace()
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
            compiled_graph = f(self, *args, **kwargs)
            if hasattr(self, "__dict__"):
                compiled_graph._sentry_source_graph = self
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

        graph_name, node_names = _get_graph_metadata(self)

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

            # Capture input state if PII is allowed
            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MESSAGES, args[0])

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

        graph_name, node_names = _get_graph_metadata(self)

        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=(
                f"invoke_agent ainvoke {graph_name}".strip()
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

            # Capture input state if PII is allowed
            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MESSAGES, args[0])

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
