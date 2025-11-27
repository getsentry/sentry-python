import contextvars
import itertools
import warnings
from collections import OrderedDict
from functools import wraps
import sys

import sentry_sdk
from sentry_sdk.ai.monitoring import set_ai_pipeline_name
from sentry_sdk.ai.utils import (
    GEN_AI_ALLOWED_MESSAGE_ROLES,
    normalize_message_roles,
    set_data_normalized,
    get_start_span_function,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import _get_value, set_span_errored
from sentry_sdk.utils import logger, capture_internal_exceptions

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import (
        Any,
        AsyncIterator,
        Callable,
        Dict,
        Iterator,
        List,
        Optional,
        Union,
    )
    from uuid import UUID
    from sentry_sdk.tracing import Span


try:
    from langchain_core.agents import AgentFinish
    from langchain_core.callbacks import (
        BaseCallbackHandler,
        BaseCallbackManager,
        Callbacks,
        manager,
    )
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult

except ImportError:
    raise DidNotEnable("langchain not installed")


try:
    # >=v1
    from langchain_classic.agents import AgentExecutor  # type: ignore[import-not-found]
except ImportError:
    try:
        # <v1
        from langchain.agents import AgentExecutor
    except ImportError:
        AgentExecutor = None


# Conditional imports for embeddings providers
try:
    from langchain_openai import OpenAIEmbeddings  # type: ignore[import-not-found]
except ImportError:
    OpenAIEmbeddings = None

try:
    from langchain_openai import AzureOpenAIEmbeddings
except ImportError:
    AzureOpenAIEmbeddings = None

try:
    from langchain_google_vertexai import VertexAIEmbeddings  # type: ignore[import-not-found]
except ImportError:
    VertexAIEmbeddings = None

try:
    from langchain_aws import BedrockEmbeddings  # type: ignore[import-not-found]
except ImportError:
    BedrockEmbeddings = None

try:
    from langchain_cohere import CohereEmbeddings  # type: ignore[import-not-found]
except ImportError:
    CohereEmbeddings = None

try:
    from langchain_mistralai import MistralAIEmbeddings  # type: ignore[import-not-found]
except ImportError:
    MistralAIEmbeddings = None

try:
    from langchain_huggingface import HuggingFaceEmbeddings  # type: ignore[import-not-found]
except ImportError:
    HuggingFaceEmbeddings = None

try:
    from langchain_ollama import OllamaEmbeddings  # type: ignore[import-not-found]
except ImportError:
    OllamaEmbeddings = None


DATA_FIELDS = {
    "frequency_penalty": SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
    "function_call": SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
    "max_tokens": SPANDATA.GEN_AI_REQUEST_MAX_TOKENS,
    "presence_penalty": SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
    "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
    "tool_calls": SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
    "top_k": SPANDATA.GEN_AI_REQUEST_TOP_K,
    "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
}


# Contextvar to track agent names in a stack for re-entrant agent support
_agent_stack = contextvars.ContextVar("langchain_agent_stack", default=None)  # type: contextvars.ContextVar[Optional[List[Optional[str]]]]


def _push_agent(agent_name):
    # type: (Optional[str]) -> None
    """Push an agent name onto the stack."""
    stack = _agent_stack.get()
    if stack is None:
        stack = []
    else:
        # Copy the list to maintain contextvar isolation across async contexts
        stack = stack.copy()
    stack.append(agent_name)
    _agent_stack.set(stack)


def _pop_agent():
    # type: () -> Optional[str]
    """Pop an agent name from the stack and return it."""
    stack = _agent_stack.get()
    if stack:
        # Copy the list to maintain contextvar isolation across async contexts
        stack = stack.copy()
        agent_name = stack.pop()
        _agent_stack.set(stack)
        return agent_name
    return None


def _get_current_agent():
    # type: () -> Optional[str]
    """Get the current agent name (top of stack) without removing it."""
    stack = _agent_stack.get()
    if stack:
        return stack[-1]
    return None


class LangchainIntegration(Integration):
    identifier = "langchain"
    origin = f"auto.ai.{identifier}"

    def __init__(self, include_prompts=True, max_spans=None):
        # type: (LangchainIntegration, bool, Optional[int]) -> None
        self.include_prompts = include_prompts
        self.max_spans = max_spans

        if max_spans is not None:
            warnings.warn(
                "The `max_spans` parameter of `LangchainIntegration` is "
                "deprecated and will be removed in version 3.0 of sentry-sdk.",
                DeprecationWarning,
                stacklevel=2,
            )

    @staticmethod
    def setup_once():
        # type: () -> None
        manager._configure = _wrap_configure(manager._configure)

        if AgentExecutor is not None:
            AgentExecutor.invoke = _wrap_agent_executor_invoke(AgentExecutor.invoke)
            AgentExecutor.stream = _wrap_agent_executor_stream(AgentExecutor.stream)

        # Patch embeddings providers
        _patch_embeddings_provider(OpenAIEmbeddings)
        _patch_embeddings_provider(AzureOpenAIEmbeddings)
        _patch_embeddings_provider(VertexAIEmbeddings)
        _patch_embeddings_provider(BedrockEmbeddings)
        _patch_embeddings_provider(CohereEmbeddings)
        _patch_embeddings_provider(MistralAIEmbeddings)
        _patch_embeddings_provider(HuggingFaceEmbeddings)
        _patch_embeddings_provider(OllamaEmbeddings)


class WatchedSpan:
    span = None  # type: Span
    children = []  # type: List[WatchedSpan]
    is_pipeline = False  # type: bool

    def __init__(self, span):
        # type: (Span) -> None
        self.span = span


class SentryLangchainCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Callback handler that creates Sentry spans."""

    def __init__(self, max_span_map_size, include_prompts):
        # type: (Optional[int], bool) -> None
        self.span_map = OrderedDict()  # type: OrderedDict[UUID, WatchedSpan]
        self.max_span_map_size = max_span_map_size
        self.include_prompts = include_prompts

    def gc_span_map(self):
        # type: () -> None

        if self.max_span_map_size is not None:
            while len(self.span_map) > self.max_span_map_size:
                run_id, watched_span = self.span_map.popitem(last=False)
                self._exit_span(watched_span, run_id)

    def _handle_error(self, run_id, error):
        # type: (UUID, Any) -> None
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            span = span_data.span
            set_span_errored(span)

            sentry_sdk.capture_exception(error, span.scope)

            span.__exit__(None, None, None)
            del self.span_map[run_id]

    def _normalize_langchain_message(self, message):
        # type: (BaseMessage) -> Any
        parsed = {"role": message.type, "content": message.content}
        parsed.update(message.additional_kwargs)
        return parsed

    def _create_span(self, run_id, parent_id, **kwargs):
        # type: (SentryLangchainCallback, UUID, Optional[Any], Any) -> WatchedSpan
        watched_span = None  # type: Optional[WatchedSpan]
        if parent_id:
            parent_span = self.span_map.get(parent_id)  # type: Optional[WatchedSpan]
            if parent_span:
                watched_span = WatchedSpan(parent_span.span.start_child(**kwargs))
                parent_span.children.append(watched_span)

        if watched_span is None:
            watched_span = WatchedSpan(sentry_sdk.start_span(**kwargs))

        watched_span.span.__enter__()
        self.span_map[run_id] = watched_span
        self.gc_span_map()
        return watched_span

    def _exit_span(self, span_data, run_id):
        # type: (SentryLangchainCallback, WatchedSpan, UUID) -> None
        if span_data.is_pipeline:
            set_ai_pipeline_name(None)

        span_data.span.__exit__(None, None, None)
        del self.span_map[run_id]

    def on_llm_start(
        self,
        serialized,
        prompts,
        *,
        run_id,
        tags=None,
        parent_run_id=None,
        metadata=None,
        **kwargs,
    ):
        # type: (SentryLangchainCallback, Dict[str, Any], List[str], UUID, Optional[List[str]], Optional[UUID], Optional[Dict[str, Any]], Any) -> Any
        """Run when LLM starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            all_params = kwargs.get("invocation_params", {})
            all_params.update(serialized.get("kwargs", {}))

            model = (
                all_params.get("model")
                or all_params.get("model_name")
                or all_params.get("model_id")
                or ""
            )

            watched_span = self._create_span(
                run_id,
                parent_run_id,
                op=OP.GEN_AI_PIPELINE,
                name=kwargs.get("name") or "Langchain LLM call",
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span

            if model:
                span.set_data(
                    SPANDATA.GEN_AI_REQUEST_MODEL,
                    model,
                )

            ai_type = all_params.get("_type", "")
            if "anthropic" in ai_type:
                span.set_data(SPANDATA.GEN_AI_SYSTEM, "anthropic")
            elif "openai" in ai_type:
                span.set_data(SPANDATA.GEN_AI_SYSTEM, "openai")

            for key, attribute in DATA_FIELDS.items():
                if key in all_params and all_params[key] is not None:
                    set_data_normalized(span, attribute, all_params[key], unpack=False)

            _set_tools_on_span(span, all_params.get("tools"))

            if should_send_default_pii() and self.include_prompts:
                normalized_messages = [
                    {
                        "role": GEN_AI_ALLOWED_MESSAGE_ROLES.USER,
                        "content": {"type": "text", "text": prompt},
                    }
                    for prompt in prompts
                ]
                scope = sentry_sdk.get_current_scope()
                messages_data = truncate_and_annotate_messages(
                    normalized_messages, span, scope
                )
                if messages_data is not None:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        messages_data,
                        unpack=False,
                    )

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], List[List[BaseMessage]], UUID, Any) -> Any
        """Run when Chat Model starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            all_params = kwargs.get("invocation_params", {})
            all_params.update(serialized.get("kwargs", {}))

            model = (
                all_params.get("model")
                or all_params.get("model_name")
                or all_params.get("model_id")
                or ""
            )

            watched_span = self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.GEN_AI_CHAT,
                name=f"chat {model}".strip(),
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
            if model:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)

            ai_type = all_params.get("_type", "")
            if "anthropic" in ai_type:
                span.set_data(SPANDATA.GEN_AI_SYSTEM, "anthropic")
            elif "openai" in ai_type:
                span.set_data(SPANDATA.GEN_AI_SYSTEM, "openai")

            agent_name = _get_current_agent()
            if agent_name:
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_name)

            for key, attribute in DATA_FIELDS.items():
                if key in all_params and all_params[key] is not None:
                    set_data_normalized(span, attribute, all_params[key], unpack=False)

            _set_tools_on_span(span, all_params.get("tools"))

            if should_send_default_pii() and self.include_prompts:
                normalized_messages = []
                for list_ in messages:
                    for message in list_:
                        normalized_messages.append(
                            self._normalize_langchain_message(message)
                        )
                normalized_messages = normalize_message_roles(normalized_messages)
                scope = sentry_sdk.get_current_scope()
                messages_data = truncate_and_annotate_messages(
                    normalized_messages, span, scope
                )
                if messages_data is not None:
                    set_data_normalized(
                        span,
                        SPANDATA.GEN_AI_REQUEST_MESSAGES,
                        messages_data,
                        unpack=False,
                    )

    def on_chat_model_end(self, response, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, LLMResult, UUID, Any) -> Any
        """Run when Chat Model ends running."""
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            span = span_data.span

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    [[x.text for x in list_] for list_ in response.generations],
                )

            _record_token_usage(span, response)
            self._exit_span(span_data, run_id)

    def on_llm_end(self, response, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, LLMResult, UUID, Any) -> Any
        """Run when LLM ends running."""
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            span = span_data.span

            try:
                generation = response.generations[0][0]
            except IndexError:
                generation = None

            if generation is not None:
                try:
                    response_model = generation.message.response_metadata.get(
                        "model_name"
                    )
                    if response_model is not None:
                        span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)
                except AttributeError:
                    pass

                try:
                    finish_reason = generation.generation_info.get("finish_reason")
                    if finish_reason is not None:
                        span.set_data(
                            SPANDATA.GEN_AI_RESPONSE_FINISH_REASONS, finish_reason
                        )
                except AttributeError:
                    pass

                try:
                    if should_send_default_pii() and self.include_prompts:
                        tool_calls = getattr(generation.message, "tool_calls", None)
                        if tool_calls is not None and tool_calls != []:
                            set_data_normalized(
                                span,
                                SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
                                tool_calls,
                                unpack=False,
                            )
                except AttributeError:
                    pass

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    [[x.text for x in list_] for list_ in response.generations],
                )

            _record_token_usage(span, response)
            self._exit_span(span_data, run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when LLM errors."""
        self._handle_error(run_id, error)

    def on_chat_model_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when Chat Model errors."""
        self._handle_error(run_id, error)

    def on_agent_finish(self, finish, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, AgentFinish, UUID, Any) -> Any
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            span = span_data.span

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span, SPANDATA.GEN_AI_RESPONSE_TEXT, finish.return_values.items()
                )

            self._exit_span(span_data, run_id)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], str, UUID, Any) -> Any
        """Run when tool starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            tool_name = serialized.get("name") or kwargs.get("name") or ""

            watched_span = self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.GEN_AI_EXECUTE_TOOL,
                name=f"execute_tool {tool_name}".strip(),
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
            span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)

            tool_description = serialized.get("description")
            if tool_description is not None:
                span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool_description)

            agent_name = _get_current_agent()
            if agent_name:
                span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_name)

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_TOOL_INPUT,
                    kwargs.get("inputs", [input_str]),
                )

    def on_tool_end(self, output, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, str, UUID, Any) -> Any
        """Run when tool ends running."""
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            span = span_data.span

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(span, SPANDATA.GEN_AI_TOOL_OUTPUT, output)

            self._exit_span(span_data, run_id)

    def on_tool_error(self, error, *args, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when tool errors."""
        self._handle_error(run_id, error)


def _extract_tokens(token_usage):
    # type: (Any) -> tuple[Optional[int], Optional[int], Optional[int]]
    if not token_usage:
        return None, None, None

    input_tokens = _get_value(token_usage, "prompt_tokens") or _get_value(
        token_usage, "input_tokens"
    )
    output_tokens = _get_value(token_usage, "completion_tokens") or _get_value(
        token_usage, "output_tokens"
    )
    total_tokens = _get_value(token_usage, "total_tokens")

    return input_tokens, output_tokens, total_tokens


def _extract_tokens_from_generations(generations):
    # type: (Any) -> tuple[Optional[int], Optional[int], Optional[int]]
    """Extract token usage from response.generations structure."""
    if not generations:
        return None, None, None

    total_input = 0
    total_output = 0
    total_total = 0

    for gen_list in generations:
        for gen in gen_list:
            token_usage = _get_token_usage(gen)
            input_tokens, output_tokens, total_tokens = _extract_tokens(token_usage)
            total_input += input_tokens if input_tokens is not None else 0
            total_output += output_tokens if output_tokens is not None else 0
            total_total += total_tokens if total_tokens is not None else 0

    return (
        total_input if total_input > 0 else None,
        total_output if total_output > 0 else None,
        total_total if total_total > 0 else None,
    )


def _get_token_usage(obj):
    # type: (Any) -> Optional[Dict[str, Any]]
    """
    Check multiple paths to extract token usage from different objects.
    """
    possible_names = ("usage", "token_usage", "usage_metadata")

    message = _get_value(obj, "message")
    if message is not None:
        for name in possible_names:
            usage = _get_value(message, name)
            if usage is not None:
                return usage

    llm_output = _get_value(obj, "llm_output")
    if llm_output is not None:
        for name in possible_names:
            usage = _get_value(llm_output, name)
            if usage is not None:
                return usage

    for name in possible_names:
        usage = _get_value(obj, name)
        if usage is not None:
            return usage

    return None


def _record_token_usage(span, response):
    # type: (Span, Any) -> None
    token_usage = _get_token_usage(response)
    if token_usage:
        input_tokens, output_tokens, total_tokens = _extract_tokens(token_usage)
    else:
        input_tokens, output_tokens, total_tokens = _extract_tokens_from_generations(
            response.generations
        )

    if input_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)

    if output_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)

    if total_tokens is not None:
        span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)


def _get_request_data(obj, args, kwargs):
    # type: (Any, Any, Any) -> tuple[Optional[str], Optional[List[Any]]]
    """
    Get the agent name and available tools for the agent.
    """
    agent = getattr(obj, "agent", None)
    runnable = getattr(agent, "runnable", None)
    runnable_config = getattr(runnable, "config", {})
    tools = (
        getattr(obj, "tools", None)
        or getattr(agent, "tools", None)
        or runnable_config.get("tools")
        or runnable_config.get("available_tools")
    )
    tools = tools if tools and len(tools) > 0 else None

    try:
        agent_name = None
        if len(args) > 1:
            agent_name = args[1].get("run_name")
        if agent_name is None:
            agent_name = runnable_config.get("run_name")
    except Exception:
        pass

    return (agent_name, tools)


def _simplify_langchain_tools(tools):
    # type: (Any) -> Optional[List[Any]]
    """Parse and simplify tools into a cleaner format."""
    if not tools:
        return None

    if not isinstance(tools, (list, tuple)):
        return None

    simplified_tools = []
    for tool in tools:
        try:
            if isinstance(tool, dict):
                if "function" in tool and isinstance(tool["function"], dict):
                    func = tool["function"]
                    simplified_tool = {
                        "name": func.get("name"),
                        "description": func.get("description"),
                    }
                    if simplified_tool["name"]:
                        simplified_tools.append(simplified_tool)
                elif "name" in tool:
                    simplified_tool = {
                        "name": tool.get("name"),
                        "description": tool.get("description"),
                    }
                    simplified_tools.append(simplified_tool)
                else:
                    name = (
                        tool.get("name")
                        or tool.get("tool_name")
                        or tool.get("function_name")
                    )
                    if name:
                        simplified_tools.append(
                            {
                                "name": name,
                                "description": tool.get("description")
                                or tool.get("desc"),
                            }
                        )
            elif hasattr(tool, "name"):
                simplified_tool = {
                    "name": getattr(tool, "name", None),
                    "description": getattr(tool, "description", None)
                    or getattr(tool, "desc", None),
                }
                if simplified_tool["name"]:
                    simplified_tools.append(simplified_tool)
            elif hasattr(tool, "__name__"):
                simplified_tools.append(
                    {
                        "name": tool.__name__,
                        "description": getattr(tool, "__doc__", None),
                    }
                )
            else:
                tool_str = str(tool)
                if tool_str and tool_str != "":
                    simplified_tools.append({"name": tool_str, "description": None})
        except Exception:
            continue

    return simplified_tools if simplified_tools else None


def _set_tools_on_span(span, tools):
    # type: (Span, Any) -> None
    """Set available tools data on a span if tools are provided."""
    if tools is not None:
        simplified_tools = _simplify_langchain_tools(tools)
        if simplified_tools:
            set_data_normalized(
                span,
                SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
                simplified_tools,
                unpack=False,
            )


def _wrap_configure(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_configure(
        callback_manager_cls,  # type: type
        inheritable_callbacks=None,  # type: Callbacks
        local_callbacks=None,  # type: Callbacks
        *args,  # type: Any
        **kwargs,  # type: Any
    ):
        # type: (...) -> Any

        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(
                callback_manager_cls,
                inheritable_callbacks,
                local_callbacks,
                *args,
                **kwargs,
            )

        local_callbacks = local_callbacks or []

        # Handle each possible type of local_callbacks. For each type, we
        # extract the list of callbacks to check for SentryLangchainCallback,
        # and define a function that would add the SentryLangchainCallback
        # to the existing callbacks list.
        if isinstance(local_callbacks, BaseCallbackManager):
            callbacks_list = local_callbacks.handlers
        elif isinstance(local_callbacks, BaseCallbackHandler):
            callbacks_list = [local_callbacks]
        elif isinstance(local_callbacks, list):
            callbacks_list = local_callbacks
        else:
            logger.debug("Unknown callback type: %s", local_callbacks)
            # Just proceed with original function call
            return f(
                callback_manager_cls,
                inheritable_callbacks,
                local_callbacks,
                *args,
                **kwargs,
            )

        # Handle each possible type of inheritable_callbacks.
        if isinstance(inheritable_callbacks, BaseCallbackManager):
            inheritable_callbacks_list = inheritable_callbacks.handlers
        elif isinstance(inheritable_callbacks, list):
            inheritable_callbacks_list = inheritable_callbacks
        else:
            inheritable_callbacks_list = []

        if not any(
            isinstance(cb, SentryLangchainCallback)
            for cb in itertools.chain(callbacks_list, inheritable_callbacks_list)
        ):
            sentry_handler = SentryLangchainCallback(
                integration.max_spans,
                integration.include_prompts,
            )
            if isinstance(local_callbacks, BaseCallbackManager):
                local_callbacks = local_callbacks.copy()
                local_callbacks.handlers = [
                    *local_callbacks.handlers,
                    sentry_handler,
                ]
            elif isinstance(local_callbacks, BaseCallbackHandler):
                local_callbacks = [local_callbacks, sentry_handler]
            else:
                local_callbacks = [*local_callbacks, sentry_handler]

        return f(
            callback_manager_cls,
            inheritable_callbacks,
            local_callbacks,
            *args,
            **kwargs,
        )

    return new_configure


def _wrap_agent_executor_invoke(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_invoke(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        agent_name, tools = _get_request_data(self, args, kwargs)
        start_span_function = get_start_span_function()

        with start_span_function(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=f"invoke_agent {agent_name}" if agent_name else "invoke_agent",
            origin=LangchainIntegration.origin,
        ) as span:
            _push_agent(agent_name)
            try:
                if agent_name:
                    span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_name)

                span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
                span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, False)

                _set_tools_on_span(span, tools)

                # Run the agent
                result = f(self, *args, **kwargs)

                input = result.get("input")
                if (
                    input is not None
                    and should_send_default_pii()
                    and integration.include_prompts
                ):
                    normalized_messages = normalize_message_roles([input])
                    scope = sentry_sdk.get_current_scope()
                    messages_data = truncate_and_annotate_messages(
                        normalized_messages, span, scope
                    )
                    if messages_data is not None:
                        set_data_normalized(
                            span,
                            SPANDATA.GEN_AI_REQUEST_MESSAGES,
                            messages_data,
                            unpack=False,
                        )

                output = result.get("output")
                if (
                    output is not None
                    and should_send_default_pii()
                    and integration.include_prompts
                ):
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, output)

                return result
            finally:
                # Ensure agent is popped even if an exception occurs
                _pop_agent()

    return new_invoke


def _wrap_agent_executor_stream(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_stream(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        agent_name, tools = _get_request_data(self, args, kwargs)
        start_span_function = get_start_span_function()

        span = start_span_function(
            op=OP.GEN_AI_INVOKE_AGENT,
            name=f"invoke_agent {agent_name}" if agent_name else "invoke_agent",
            origin=LangchainIntegration.origin,
        )
        span.__enter__()

        _push_agent(agent_name)

        if agent_name:
            span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent_name)

        span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
        span.set_data(SPANDATA.GEN_AI_RESPONSE_STREAMING, True)

        _set_tools_on_span(span, tools)

        input = args[0].get("input") if len(args) >= 1 else None
        if (
            input is not None
            and should_send_default_pii()
            and integration.include_prompts
        ):
            normalized_messages = normalize_message_roles([input])
            scope = sentry_sdk.get_current_scope()
            messages_data = truncate_and_annotate_messages(
                normalized_messages, span, scope
            )
            if messages_data is not None:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    messages_data,
                    unpack=False,
                )

        # Run the agent
        result = f(self, *args, **kwargs)

        old_iterator = result

        def new_iterator():
            # type: () -> Iterator[Any]
            exc_info = (None, None, None)  # type: tuple[Any, Any, Any]
            try:
                for event in old_iterator:
                    yield event

                try:
                    output = event.get("output")
                except Exception:
                    output = None

                if (
                    output is not None
                    and should_send_default_pii()
                    and integration.include_prompts
                ):
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, output)
            except Exception:
                exc_info = sys.exc_info()
                set_span_errored(span)
                raise
            finally:
                # Ensure cleanup happens even if iterator is abandoned or fails
                _pop_agent()
                span.__exit__(*exc_info)

        async def new_iterator_async():
            # type: () -> AsyncIterator[Any]
            exc_info = (None, None, None)  # type: tuple[Any, Any, Any]
            try:
                async for event in old_iterator:
                    yield event

                try:
                    output = event.get("output")
                except Exception:
                    output = None

                if (
                    output is not None
                    and should_send_default_pii()
                    and integration.include_prompts
                ):
                    set_data_normalized(span, SPANDATA.GEN_AI_RESPONSE_TEXT, output)
            except Exception:
                exc_info = sys.exc_info()
                set_span_errored(span)
                raise
            finally:
                # Ensure cleanup happens even if iterator is abandoned or fails
                _pop_agent()
                span.__exit__(*exc_info)

        if str(type(result)) == "<class 'async_generator'>":
            result = new_iterator_async()
        else:
            result = new_iterator()

        return result

    return new_stream


def _patch_embeddings_provider(provider_class):
    # type: (Any) -> None
    """Patch an embeddings provider class with monitoring wrappers."""
    if provider_class is None:
        return

    if hasattr(provider_class, "embed_documents"):
        provider_class.embed_documents = _wrap_embedding_method(
            provider_class.embed_documents
        )
    if hasattr(provider_class, "embed_query"):
        provider_class.embed_query = _wrap_embedding_method(provider_class.embed_query)
    if hasattr(provider_class, "aembed_documents"):
        provider_class.aembed_documents = _wrap_async_embedding_method(
            provider_class.aembed_documents
        )
    if hasattr(provider_class, "aembed_query"):
        provider_class.aembed_query = _wrap_async_embedding_method(
            provider_class.aembed_query
        )


def _wrap_embedding_method(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """Wrap sync embedding methods (embed_documents and embed_query)."""

    @wraps(f)
    def new_embedding_method(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        model_name = getattr(self, "model", None) or getattr(self, "model_name", None)
        with sentry_sdk.start_span(
            op=OP.GEN_AI_EMBEDDINGS,
            name=f"embeddings {model_name}" if model_name else "embeddings",
            origin=LangchainIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "embeddings")
            if model_name:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

            # Capture input if PII is allowed
            if (
                should_send_default_pii()
                and integration.include_prompts
                and len(args) > 0
            ):
                input_data = args[0]
                # Normalize to list format
                texts = input_data if isinstance(input_data, list) else [input_data]
                set_data_normalized(
                    span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, texts, unpack=False
                )

            result = f(self, *args, **kwargs)
            return result

    return new_embedding_method


def _wrap_async_embedding_method(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]
    """Wrap async embedding methods (aembed_documents and aembed_query)."""

    @wraps(f)
    async def new_async_embedding_method(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return await f(self, *args, **kwargs)

        model_name = getattr(self, "model", None) or getattr(self, "model_name", None)
        with sentry_sdk.start_span(
            op=OP.GEN_AI_EMBEDDINGS,
            name=f"embeddings {model_name}" if model_name else "embeddings",
            origin=LangchainIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "embeddings")
            if model_name:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

            # Capture input if PII is allowed
            if (
                should_send_default_pii()
                and integration.include_prompts
                and len(args) > 0
            ):
                input_data = args[0]
                # Normalize to list format
                texts = input_data if isinstance(input_data, list) else [input_data]
                set_data_normalized(
                    span, SPANDATA.GEN_AI_EMBEDDINGS_INPUT, texts, unpack=False
                )

            result = await f(self, *args, **kwargs)
            return result

    return new_async_embedding_method
