from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

import sentry_sdk
from sentry_sdk.ai.utils import (
    set_data_normalized,
    normalize_message_roles,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import _get_value
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


def _normalize_langgraph_message(message):
    # type: (Any) -> Any
    if not hasattr(message, "content"):
        return None

    parsed = {"role": getattr(message, "type", None), "content": message.content}

    for attr in ["name", "tool_calls", "function_call", "tool_call_id"]:
        if hasattr(message, attr):
            value = getattr(message, attr)
            if value is not None:
                parsed[attr] = value

    return parsed


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
            normalized = _normalize_langgraph_message(message)
            if normalized:
                normalized_messages.append(normalized)
        except Exception:
            continue

    return normalized_messages if normalized_messages else None


def _extract_model_from_config(config):
    # type: (Any) -> Optional[str]
    if not config:
        return None

    if isinstance(config, dict):
        model = config.get("model")
        if model:
            return str(model)

        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            model = configurable.get("model")
            if model:
                return str(model)

    if hasattr(config, "model"):
        return str(config.model)

    if hasattr(config, "configurable"):
        configurable = config.configurable
        if isinstance(configurable, dict):
            model = configurable.get("model")
            if model:
                return str(model)
        elif hasattr(configurable, "model"):
            return str(configurable.model)

    return None


def _extract_model_from_pregel(pregel_instance):
    # type: (Any) -> Optional[str]
    if hasattr(pregel_instance, "config"):
        model = _extract_model_from_config(pregel_instance.config)
        if model:
            return model

    if hasattr(pregel_instance, "nodes"):
        nodes = pregel_instance.nodes
        if isinstance(nodes, dict):
            for node_name, node in nodes.items():
                if hasattr(node, "bound") and hasattr(node.bound, "model_name"):
                    return str(node.bound.model_name)
                if hasattr(node, "runnable") and hasattr(node.runnable, "model_name"):
                    return str(node.runnable.model_name)

    return None


def _get_token_usage(obj):
    # type: (Any) -> Optional[Dict[str, Any]]
    possible_names = ("usage", "token_usage", "usage_metadata")

    for name in possible_names:
        usage = _get_value(obj, name)
        if usage is not None:
            return usage

    if isinstance(obj, dict):
        messages = obj.get("messages", [])
        if messages and isinstance(messages, list):
            for message in reversed(messages):
                for name in possible_names:
                    usage = _get_value(message, name)
                    if usage is not None:
                        return usage

    return None


def _extract_tokens(token_usage):
    # type: (Any) -> Tuple[Optional[int], Optional[int], Optional[int]]
    input_tokens = _get_value(token_usage, "prompt_tokens") or _get_value(
        token_usage, "input_tokens"
    )
    output_tokens = _get_value(token_usage, "completion_tokens") or _get_value(
        token_usage, "output_tokens"
    )
    total_tokens = _get_value(token_usage, "total_tokens")

    return input_tokens, output_tokens, total_tokens


def _record_token_usage(span, response):
    # type: (Any, Any) -> None
    token_usage = _get_token_usage(response)
    if not token_usage:
        return

    input_tokens, output_tokens, total_tokens = _extract_tokens(token_usage)

    if input_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)

    if output_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)

    if total_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)


def _extract_model_from_response(result):
    # type: (Any) -> Optional[str]
    if isinstance(result, dict):
        messages = result.get("messages", [])
        if messages and isinstance(messages, list):
            for message in reversed(messages):
                if hasattr(message, "response_metadata"):
                    metadata = message.response_metadata
                    if isinstance(metadata, dict):
                        model = metadata.get("model")
                        if model:
                            return str(model)
                        model_name = metadata.get("model_name")
                        if model_name:
                            return str(model_name)

    return None


def _wrap_state_graph_compile(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    @wraps(f)
    def new_compile(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LanggraphIntegration)
        if integration is None:
            return f(self, *args, **kwargs)
        with sentry_sdk.start_span(
            op=OP.GEN_AI_CREATE_AGENT,
            origin=LanggraphIntegration.origin,
        ) as span:
            compiled_graph = f(self, *args, **kwargs)

            compiled_graph_name = getattr(compiled_graph, "name", None)
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "create_agent")
            span.set_data(SPANDATA.GEN_AI_AGENT_NAME, compiled_graph_name)

            if compiled_graph_name:
                span.description = f"create_agent {compiled_graph_name}"
            else:
                span.description = "create_agent"

            if kwargs.get("model", None) is not None:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, kwargs.get("model"))

            tools = None
            get_graph = getattr(compiled_graph, "get_graph", None)
            if get_graph and callable(get_graph):
                graph_obj = compiled_graph.get_graph()
                nodes = getattr(graph_obj, "nodes", None)
                if nodes and isinstance(nodes, dict):
                    tools_node = nodes.get("tools")
                    if tools_node:
                        data = getattr(tools_node, "data", None)
                        if data and hasattr(data, "tools_by_name"):
                            tools = list(data.tools_by_name.keys())

            if tools is not None:
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
        span_name = (
            f"invoke_agent {graph_name}".strip() if graph_name else "invoke_agent"
        )

        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=span_name,
            origin=LanggraphIntegration.origin,
        ) as span:
            if graph_name:
                span.set_data(SPANDATA.GEN_AI_PIPELINE_NAME, graph_name)
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, graph_name)

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

            request_model = _extract_model_from_pregel(self)
            if not request_model and len(kwargs) > 0:
                config = kwargs.get("config")
                request_model = _extract_model_from_config(config)

            if request_model:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, request_model)

            input_messages = None
            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                input_messages = _parse_langgraph_messages(args[0])
                if input_messages:
                    normalized_input_messages = normalize_message_roles(input_messages)
                    scope = sentry_sdk.get_current_scope()
                    messages_data = truncate_and_annotate_messages(
                        normalized_input_messages, span, scope
                    )
                    if messages_data is not None:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_REQUEST_MESSAGES,
                            messages_data,
                            unpack=False,
                        )

            result = f(self, *args, **kwargs)

            response_model = _extract_model_from_response(result)
            if response_model:
                span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)
            elif request_model:
                span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, request_model)

            _record_token_usage(span, result)

            _set_response_attributes(span, input_messages, result, integration)

            return result

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
        span_name = (
            f"invoke_agent {graph_name}".strip() if graph_name else "invoke_agent"
        )

        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=span_name,
            origin=LanggraphIntegration.origin,
        ) as span:
            if graph_name:
                span.set_data(SPANDATA.GEN_AI_PIPELINE_NAME, graph_name)
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, graph_name)

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

            request_model = _extract_model_from_pregel(self)
            if not request_model and len(kwargs) > 0:
                config = kwargs.get("config")
                request_model = _extract_model_from_config(config)

            if request_model:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, request_model)

            input_messages = None
            if (
                len(args) > 0
                and should_send_default_pii()
                and integration.include_prompts
            ):
                input_messages = _parse_langgraph_messages(args[0])
                if input_messages:
                    normalized_input_messages = normalize_message_roles(input_messages)
                    scope = sentry_sdk.get_current_scope()
                    messages_data = truncate_and_annotate_messages(
                        normalized_input_messages, span, scope
                    )
                    if messages_data is not None:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_REQUEST_MESSAGES,
                            messages_data,
                            unpack=False,
                        )

            result = await f(self, *args, **kwargs)

            response_model = _extract_model_from_response(result)
            if response_model:
                span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)
            elif request_model:
                span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, request_model)

            _record_token_usage(span, result)

            _set_response_attributes(span, input_messages, result, integration)

            return result

    return new_ainvoke


def _get_new_messages(input_messages, output_messages):
    # type: (Optional[List[Any]], Optional[List[Any]]) -> Optional[List[Any]]
    """Extract only the new messages added during this invocation."""
    if not output_messages:
        return None

    if not input_messages:
        return output_messages

    # only return the new messages, aka the output messages that are not in the input messages
    input_count = len(input_messages)
    new_messages = (
        output_messages[input_count:] if len(output_messages) > input_count else []
    )

    return new_messages if new_messages else None


def _extract_llm_response_text(messages):
    # type: (Optional[List[Any]]) -> Optional[str]
    if not messages:
        return None

    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role")
            if role in ["assistant", "ai"]:
                content = message.get("content")
                if content and isinstance(content, str):
                    return content

    return None


def _extract_tool_calls(messages):
    # type: (Optional[List[Any]]) -> Optional[List[Any]]
    if not messages:
        return None

    tool_calls = []
    for message in messages:
        if isinstance(message, dict):
            msg_tool_calls = message.get("tool_calls")
            if msg_tool_calls and isinstance(msg_tool_calls, list):
                tool_calls.extend(msg_tool_calls)

    return tool_calls if tool_calls else None


def _set_response_attributes(span, input_messages, result, integration):
    # type: (Any, Optional[List[Any]], Any, LanggraphIntegration) -> None
    if not (should_send_default_pii() and integration.include_prompts):
        return

    parsed_response_messages = _parse_langgraph_messages(result)
    new_messages = _get_new_messages(input_messages, parsed_response_messages)

    llm_response_text = _extract_llm_response_text(new_messages)
    if llm_response_text:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, llm_response_text)
    elif new_messages:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, new_messages)
    else:
        set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, result)

    tool_calls = _extract_tool_calls(new_messages)
    if tool_calls:
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
            safe_serialize(tool_calls),
            unpack=False,
        )
