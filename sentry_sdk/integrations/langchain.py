import itertools
from collections import OrderedDict
from functools import wraps

import sentry_sdk
from sentry_sdk.ai.monitoring import set_ai_pipeline_name, record_token_usage
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.ai.utils import set_data_normalized
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing import Span
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import logger, capture_internal_exceptions

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, List, Callable, Dict, Union, Optional
    from uuid import UUID

try:
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult
    from langchain_core.callbacks import (
        manager,
        BaseCallbackHandler,
        BaseCallbackManager,
        Callbacks,
    )
    from langchain_core.agents import AgentAction, AgentFinish
    from langchain.agents import AgentExecutor

except ImportError:
    raise DidNotEnable("langchain not installed")


DATA_FIELDS = {
    "temperature": SPANDATA.GEN_AI_REQUEST_TEMPERATURE,
    "top_p": SPANDATA.GEN_AI_REQUEST_TOP_P,
    "top_k": SPANDATA.GEN_AI_REQUEST_TOP_K,
    "function_call": SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
    "tool_calls": SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS,
    "tools": SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
    "response_format": SPANDATA.GEN_AI_RESPONSE_FORMAT,
    "logit_bias": SPANDATA.GEN_AI_REQUEST_LOGIT_BIAS,
    "tags": SPANDATA.GEN_AI_REQUEST_TAGS,
}

# TODO(shellmayr): is this still the case?
# To avoid double collecting tokens, we do *not* measure
# token counts for models for which we have an explicit integration
NO_COLLECT_TOKEN_MODELS = [
    # "openai-chat",
    # "anthropic-chat",
    "cohere-chat",
    "huggingface_endpoint",
]


class LangchainIntegration(Integration):
    identifier = "langchain"
    origin = f"auto.ai.{identifier}"

    # The most number of spans (e.g., LLM calls) that can be processed at the same time.
    max_spans = 1024

    def __init__(self, include_prompts=True, max_spans=1024):
        # type: (LangchainIntegration, bool, int) -> None
        self.include_prompts = include_prompts
        self.max_spans = max_spans

    @staticmethod
    def setup_once():
        # type: () -> None
        manager._configure = _wrap_configure(manager._configure)

        if AgentExecutor is not None:
            AgentExecutor.invoke = _wrap_agent_executor_invoke(AgentExecutor.invoke)
            AgentExecutor.stream = _wrap_agent_executor_stream(AgentExecutor.stream)


class WatchedSpan:
    span = None  # type: Span
    no_collect_tokens = False  # type: bool
    children = []  # type: List[WatchedSpan]
    is_pipeline = False  # type: bool

    def __init__(self, span):
        # type: (Span) -> None
        self.span = span


class SentryLangchainCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Base callback handler that can be used to handle callbacks from langchain."""

    def __init__(self, max_span_map_size, include_prompts):
        # type: (int, bool) -> None
        self.span_map = OrderedDict()  # type: OrderedDict[UUID, WatchedSpan]
        self.max_span_map_size = max_span_map_size
        self.include_prompts = include_prompts

    def gc_span_map(self):
        # type: () -> None

        while len(self.span_map) > self.max_span_map_size:
            run_id, watched_span = self.span_map.popitem(last=False)
            self._exit_span(watched_span, run_id)

    def _handle_error(self, run_id, error):
        # type: (UUID, Any) -> None
        if not run_id or run_id not in self.span_map:
            return

        span_data = self.span_map[run_id]
        if not span_data:
            return
        sentry_sdk.capture_exception(error, span_data.span.scope)
        span_data.span.__exit__(None, None, None)
        del self.span_map[run_id]

    def _normalize_langchain_message(self, message):
        # type: (BaseMessage) -> Any
        parsed = {"content": message.content, "role": message.type}
        parsed.update(message.additional_kwargs)
        return parsed

    def _extract_token_usage(self, token_usage):
        # type: (Any) -> tuple[Optional[int], Optional[int], Optional[int]]
        """Extract input, output, and total tokens from various token usage formats."""
        if not token_usage:
            return None, None, None

        input_tokens = None
        output_tokens = None
        total_tokens = None

        if hasattr(token_usage, "get"):
            input_tokens = token_usage.get("prompt_tokens") or token_usage.get(
                "input_tokens"
            )
            output_tokens = token_usage.get("completion_tokens") or token_usage.get(
                "output_tokens"
            )
            total_tokens = token_usage.get("total_tokens")
        else:
            input_tokens = getattr(token_usage, "prompt_tokens", None) or getattr(
                token_usage, "input_tokens", None
            )
            output_tokens = getattr(token_usage, "completion_tokens", None) or getattr(
                token_usage, "output_tokens", None
            )
            total_tokens = getattr(token_usage, "total_tokens", None)

        # LangChain's OpenAI callback uses these specific field names
        if input_tokens is None and hasattr(token_usage, "get"):
            input_tokens = token_usage.get("prompt_tokens") or token_usage.get(
                "input_tokens"
            )
        if output_tokens is None and hasattr(token_usage, "get"):
            output_tokens = token_usage.get("completion_tokens") or token_usage.get(
                "output_tokens"
            )
        if total_tokens is None and hasattr(token_usage, "get"):
            total_tokens = token_usage.get("total_tokens")

        return input_tokens, output_tokens, total_tokens

    def _extract_token_usage_from_generations(self, generations):
        # type: (Any) -> tuple[Optional[int], Optional[int], Optional[int]]
        """Extract token usage from response.generations structure."""
        if not generations:
            return None, None, None

        total_input = 0
        total_output = 0
        total_total = 0
        found = False

        for gen_list in generations:
            for gen in gen_list:
                usage_metadata = None
                if (
                    hasattr(gen, "message")
                    and getattr(gen, "message", None) is not None
                    and hasattr(gen.message, "usage_metadata")
                ):
                    usage_metadata = getattr(gen.message, "usage_metadata", None)
                if usage_metadata is None and hasattr(gen, "usage_metadata"):
                    usage_metadata = getattr(gen, "usage_metadata", None)
                if usage_metadata:
                    input_tokens, output_tokens, total_tokens = (
                        self._extract_token_usage_from_response(usage_metadata)
                    )
                    if any([input_tokens, output_tokens, total_tokens]):
                        found = True
                        total_input += int(input_tokens)
                        total_output += int(output_tokens)
                        total_total += int(total_tokens)

        if not found:
            return None, None, None

        return (
            total_input if total_input > 0 else None,
            total_output if total_output > 0 else None,
            total_total if total_total > 0 else None,
        )

    def _extract_token_usage_from_response(self, response):
        # type: (Any) -> tuple[int, int, int]
        if response:
            if hasattr(response, "get"):
                input_tokens = response.get("input_tokens", 0)
                output_tokens = response.get("output_tokens", 0)
                total_tokens = response.get("total_tokens", 0)
            else:
                input_tokens = getattr(response, "input_tokens", 0)
                output_tokens = getattr(response, "output_tokens", 0)
                total_tokens = getattr(response, "total_tokens", 0)

        return input_tokens, output_tokens, total_tokens

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

        if kwargs.get("op", "").startswith("ai.pipeline."):
            if kwargs.get("name"):
                set_ai_pipeline_name(kwargs.get("name"))
            watched_span.is_pipeline = True

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

            watched_span = self._create_span(
                run_id,
                parent_id=parent_run_id,
                op=OP.GEN_AI_PIPELINE,
                name=kwargs.get("name") or "Langchain LLM call",
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span
            span.set_data(
                SPANDATA.GEN_AI_REQUEST_MODEL,
                all_params.get(
                    "model", all_params.get("model_name", all_params.get("model_id"))
                ),
            )
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(span, SPANDATA.GEN_AI_REQUEST_MESSAGES, prompts)
            for k, v in DATA_FIELDS.items():
                if k in all_params:
                    set_data_normalized(span, v, all_params[k])

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], List[List[BaseMessage]], UUID, Any) -> Any
        """Run when Chat Model starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return
            all_params = kwargs.get("invocation_params", {})
            all_params.update(serialized.get("kwargs", {}))
            watched_span = self._create_span(
                run_id,
                parent_id=kwargs.get("parent_run_id"),
                op=OP.GEN_AI_CHAT,
                name=kwargs.get("name") or "Langchain Chat Model",
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span
            model = all_params.get(
                "model", all_params.get("model_name", all_params.get("model_id"))
            )
            watched_span.no_collect_tokens = any(
                x in all_params.get("_type", "") for x in NO_COLLECT_TOKEN_MODELS
            )

            if not model and "anthropic" in all_params.get("_type"):
                model = "claude-2"
            if model:
                span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model)
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span,
                    SPANDATA.GEN_AI_REQUEST_MESSAGES,
                    [
                        [self._normalize_langchain_message(x) for x in list_]
                        for list_ in messages
                    ],
                )
            for k, v in DATA_FIELDS.items():
                if k in all_params:
                    set_data_normalized(span, v, all_params[k])
            # no manual token counting

    def on_chat_model_end(self, response, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, LLMResult, UUID, Any) -> Any
        """Run when Chat Model ends running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            token_usage = None

            # Try multiple paths to extract token usage, prioritizing streaming-aware approaches
            if response.llm_output and "token_usage" in response.llm_output:
                token_usage = response.llm_output["token_usage"]
            elif response.llm_output and hasattr(response.llm_output, "token_usage"):
                token_usage = response.llm_output.token_usage
            elif hasattr(response, "usage"):
                token_usage = response.usage
            elif hasattr(response, "token_usage"):
                token_usage = response.token_usage
            # Check for usage_metadata in llm_output (common in streaming responses)
            elif response.llm_output and "usage_metadata" in response.llm_output:
                token_usage = response.llm_output["usage_metadata"]
            elif response.llm_output and hasattr(response.llm_output, "usage_metadata"):
                token_usage = response.llm_output.usage_metadata
            elif hasattr(response, "usage_metadata"):
                token_usage = response.usage_metadata

            span_data = self.span_map[run_id]
            if not span_data:
                return

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span_data.span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    [[x.text for x in list_] for list_ in response.generations],
                )

            if not span_data.no_collect_tokens:
                if token_usage:
                    input_tokens, output_tokens, total_tokens = (
                        self._extract_token_usage(token_usage)
                    )
                else:
                    input_tokens, output_tokens, total_tokens = (
                        self._extract_token_usage_from_generations(response.generations)
                    )

                if (
                    input_tokens is not None
                    or output_tokens is not None
                    or total_tokens is not None
                ):
                    record_token_usage(
                        span_data.span,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                    )

            self._exit_span(span_data, run_id)

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, str, UUID, Any) -> Any
        """Run on new LLM token. Only available when streaming is enabled."""
        # no manual token counting
        with capture_internal_exceptions():
            return

    def on_llm_end(self, response, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, LLMResult, UUID, Any) -> Any
        """Run when LLM ends running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            token_usage = None
            if response.llm_output and "token_usage" in response.llm_output:
                token_usage = response.llm_output["token_usage"]

            elif response.llm_output and hasattr(response.llm_output, "token_usage"):
                token_usage = response.llm_output.token_usage

            elif hasattr(response, "usage"):
                token_usage = response.usage

            elif hasattr(response, "token_usage"):
                token_usage = response.token_usage

            span_data = self.span_map[run_id]
            if not span_data:
                return

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span_data.span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    [[x.text for x in list_] for list_ in response.generations],
                )

            if not span_data.no_collect_tokens:
                if token_usage:
                    input_tokens, output_tokens, total_tokens = (
                        self._extract_token_usage(token_usage)
                    )
                else:
                    input_tokens, output_tokens, total_tokens = (
                        self._extract_token_usage_from_generations(response.generations)
                    )

                if (
                    input_tokens is not None
                    or output_tokens is not None
                    or total_tokens is not None
                ):
                    record_token_usage(
                        span_data.span,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                    )

            self._exit_span(span_data, run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when LLM errors."""
        with capture_internal_exceptions():
            self._handle_error(run_id, error)

    def on_chat_model_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when Chat Model errors."""
        with capture_internal_exceptions():
            self._handle_error(run_id, error)

    def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], Dict[str, Any], UUID, Any) -> Any
        """Run when chain starts running."""
        pass

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], UUID, Any) -> Any
        """Run when chain ends running."""
        with capture_internal_exceptions():
            if not run_id or run_id not in self.span_map:
                return

            span_data = self.span_map[run_id]
            if not span_data:
                return
            self._exit_span(span_data, run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when chain errors."""
        self._handle_error(run_id, error)

    def on_agent_action(self, action, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, AgentAction, UUID, Any) -> Any
        pass

    def on_agent_finish(self, finish, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, AgentFinish, UUID, Any) -> Any
        with capture_internal_exceptions():
            if not run_id:
                return

            span_data = self.span_map[run_id]
            if not span_data:
                return
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span_data.span,
                    SPANDATA.GEN_AI_RESPONSE_TEXT,
                    finish.return_values.items(),
                )
            self._exit_span(span_data, run_id)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], str, UUID, Any) -> Any
        """Run when tool starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            tool_name = serialized.get("name") or kwargs.get("name")

            watched_span = self._create_span(
                run_id,
                parent_id=kwargs.get("parent_run_id"),
                op=OP.GEN_AI_EXECUTE_TOOL,
                name=f"execute_tool {tool_name}",
                origin=LangchainIntegration.origin,
            )
            span = watched_span.span

            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
            span.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)

            tool_description = serialized.get("description")
            if tool_description is not None:
                span.set_data(SPANDATA.GEN_AI_TOOL_DESCRIPTION, tool_description)

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
            if not span_data:
                return
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(span_data.span, SPANDATA.GEN_AI_TOOL_OUTPUT, output)
            self._exit_span(span_data, run_id)

    def on_tool_error(self, error, *args, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when tool errors."""
        # TODO(shellmayr): how to correctly set the status when the tool fails?
        if run_id and run_id in self.span_map:
            span_data = self.span_map[run_id]
            if span_data:
                span_data.span.set_status("unknown")

        self._handle_error(run_id, error)


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
            else:  # local_callbacks is a list
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

        # Create a span that will act as the parent for all callback-generated spans
        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name="AgentExecutor.invoke",
            origin=LangchainIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            if hasattr(self, "agent") and hasattr(self.agent, "llm"):
                model_name = getattr(self.agent.llm, "model_name", None) or getattr(
                    self.agent.llm, "model", None
                )
                if model_name:
                    span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

            return f(self, *args, **kwargs)

    return new_invoke


def _wrap_agent_executor_stream(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_stream(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any

        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(self, *args, **kwargs)

        # Create a span that will act as the parent for all callback-generated spans
        with sentry_sdk.start_span(
            op=OP.GEN_AI_INVOKE_AGENT,
            name="AgentExecutor.stream",
            origin=LangchainIntegration.origin,
        ) as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            if hasattr(self, "agent") and hasattr(self.agent, "llm"):
                model_name = getattr(self.agent.llm, "model_name", None) or getattr(
                    self.agent.llm, "model", None
                )
                if model_name:
                    span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

            return f(self, *args, **kwargs)

    return new_stream
