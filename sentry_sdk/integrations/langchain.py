from collections import OrderedDict
from functools import wraps

import sentry_sdk
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations._ai_common import set_data_normalized
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing import Span

if TYPE_CHECKING:
    from typing import Any, List, Callable, Dict, Union, Optional
    from uuid import UUID
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import logger, capture_internal_exceptions

try:
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult
    from langchain_core.callbacks import (
        manager,
        BaseCallbackHandler,
    )
except ImportError:
    raise DidNotEnable("langchain not installed")


try:
    import tiktoken  # type: ignore

    enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(s):
        # type: (str) -> int
        return len(enc.encode_ordinary(s))

    logger.debug("[langchain] using tiktoken to count tokens")
except ImportError:
    logger.info(
        "The Sentry Python SDK requires 'tiktoken' in order to measure token usage from streaming langchain calls."
        "Please install 'tiktoken' if you aren't receiving accurate token usage in Sentry."
        "See https://docs.sentry.io/platforms/python/integrations/langchain/ for more information."
    )

    def count_tokens(s):
        # type: (str) -> int
        return 1


DATA_FIELDS = {
    "temperature": SPANDATA.AI_TEMPERATURE,
    "top_p": SPANDATA.AI_TOP_P,
    "top_k": SPANDATA.AI_TOP_K,
    "function_call": SPANDATA.AI_FUNCTION_CALL,
    "tool_calls": SPANDATA.AI_TOOL_CALLS,
    "tools": SPANDATA.AI_TOOLS,
    "response_format": SPANDATA.AI_RESPONSE_FORMAT,
    "logit_bias": SPANDATA.AI_LOGIT_BIAS,
}


class LangchainIntegration(Integration):
    identifier = "langchain"

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


class WatchedSpan:
    span = None  # type: Span
    num_completion_tokens = 0  # type: int
    num_prompt_tokens = 0  # type: int

    def __init__(self, span):
        # type: (Span) -> None
        self.span = span


class SentryLangchainCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Base callback handler that can be used to handle callbacks from langchain."""

    span_map = OrderedDict()  # type: OrderedDict[UUID, WatchedSpan]

    max_span_map_size = 0

    def __init__(self, max_span_map_size, include_prompts):
        # type: (int, bool) -> None
        self.max_span_map_size = max_span_map_size
        self.include_prompts = include_prompts

    def gc_span_map(self):
        # type: () -> None

        while len(self.span_map) > self.max_span_map_size:
            self.span_map.popitem(last=False)[1].span.__exit__(None, None, None)

    def _handle_error(self, run_id, error):
        # type: (UUID, Any) -> None
        if not run_id or not self.span_map[run_id]:
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

    def _create_span(self, run_id, parent_id, **kwargs):
        # type: (SentryLangchainCallback, UUID, Optional[Any], Any) -> Span

        span = None  # type: Optional[Span]
        if parent_id:
            parent_span = self.span_map[parent_id]  # type: Optional[WatchedSpan]
            if parent_span:
                span = parent_span.span.start_child(**kwargs)
        if span is None:
            span = sentry_sdk.start_span(**kwargs)

        span.__enter__()
        watched_span = WatchedSpan(span)
        self.span_map[run_id] = watched_span
        self.gc_span_map()
        return span

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
            params = kwargs.get("invocation_params", {})
            params.update(serialized.get("kwargs", {}))
            span = self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.LANGCHAIN_RUN,
                description=kwargs.get("name") or "Langchain LLM call",
            )
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(span, SPANDATA.AI_INPUT_MESSAGES, prompts)
            for k, v in DATA_FIELDS.items():
                if k in params:
                    span.set_data(v, params[k])

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], List[List[BaseMessage]], UUID, Any) -> Any
        """Run when Chat Model starts running."""
        if not run_id:
            return

        with capture_internal_exceptions():
            span = self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.LANGCHAIN_CHAT_COMPLETIONS_CREATE,
                description=kwargs.get("name") or "Langchain Chat Model",
            )
            # TODO model ids
            if should_send_default_pii() and self.include_prompts:
                span.set_data(
                    SPANDATA.AI_INPUT_MESSAGES,
                    [
                        [self._normalize_langchain_message(x) for x in list_]
                        for list_ in messages
                    ],
                )
            for list_ in messages:
                for message in list_:
                    self.span_map[run_id].num_prompt_tokens += count_tokens(
                        message.content
                    ) + count_tokens(message.type)

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, str, UUID, Any) -> Any
        """Run on new LLM token. Only available when streaming is enabled."""
        with capture_internal_exceptions():
            if not run_id or not self.span_map[run_id]:
                return
            span_data = self.span_map[run_id]
            if not span_data:
                return
            span_data.num_completion_tokens += count_tokens(token)

    def on_llm_end(self, response, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, LLMResult, UUID, Any) -> Any
        """Run when LLM ends running."""
        with capture_internal_exceptions():
            if not run_id:
                return

            token_usage = (
                response.llm_output.get("token_usage") if response.llm_output else None
            )

            span_data = self.span_map[run_id]
            if not span_data:
                return

            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span_data.span,
                    SPANDATA.AI_RESPONSES,
                    [[x.text for x in list_] for list_ in response.generations],
                )

            if token_usage:
                span_data.span.set_data(
                    SPANDATA.AI_PROMPT_TOKENS_USED, token_usage.get("prompt_tokens")
                )
                span_data.span.set_data(
                    SPANDATA.AI_COMPLETION_TOKENS_USED,
                    token_usage.get("completion_tokens"),
                )
                span_data.span.set_data(
                    SPANDATA.AI_TOTAL_TOKENS_USED, token_usage.get("total_tokens")
                )
            else:
                if span_data.num_completion_tokens:
                    span_data.span.set_data(
                        SPANDATA.AI_COMPLETION_TOKENS_USED,
                        span_data.num_completion_tokens,
                    )
                if span_data.num_prompt_tokens:
                    span_data.span.set_data(
                        SPANDATA.AI_PROMPT_TOKENS_USED, span_data.num_prompt_tokens
                    )
                if span_data.num_prompt_tokens and span_data.num_completion_tokens:
                    span_data.span.set_data(
                        SPANDATA.AI_TOTAL_TOKENS_USED,
                        span_data.num_prompt_tokens + span_data.num_completion_tokens,
                    )

            span_data.span.__exit__(None, None, None)
            del self.span_map[run_id]

    def on_llm_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when LLM errors."""
        with capture_internal_exceptions():
            self._handle_error(run_id, error)

    def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], Dict[str, Any], UUID, Any) -> Any
        """Run when chain starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return
            self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.LANGCHAIN_RUN,
                description=kwargs.get("name") or "Chain execution",
            )

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], UUID, Any) -> Any
        """Run when chain ends running."""
        with capture_internal_exceptions():
            if not run_id or not self.span_map[run_id]:
                return

            span_data = self.span_map[run_id]
            if not span_data:
                return
            span_data.span.__exit__(None, None, None)
            del self.span_map[run_id]

    def on_chain_error(self, error, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when chain errors."""
        self._handle_error(run_id, error)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, Dict[str, Any], str, UUID, Any) -> Any
        """Run when tool starts running."""
        with capture_internal_exceptions():
            if not run_id:
                return
            span = self._create_span(
                run_id,
                kwargs.get("parent_run_id"),
                op=OP.LANGCHAIN_TOOL,
                description=kwargs.get("name") or "AI tool usage",
            )
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(
                    span, SPANDATA.AI_INPUT_MESSAGES, kwargs.get("inputs", [input_str])
                )

    def on_tool_end(self, output, *, run_id, **kwargs):
        # type: (SentryLangchainCallback, str, UUID, Any) -> Any
        """Run when tool ends running."""
        with capture_internal_exceptions():
            if not run_id or not self.span_map[run_id]:
                return

            span_data = self.span_map[run_id]
            if not span_data:
                return
            if should_send_default_pii() and self.include_prompts:
                set_data_normalized(span_data.span, SPANDATA.AI_RESPONSES, [output])
            span_data.span.__exit__(None, None, None)
            del self.span_map[run_id]

    def on_tool_error(self, error, *args, run_id, **kwargs):
        # type: (SentryLangchainCallback, Union[Exception, KeyboardInterrupt], UUID, Any) -> Any
        """Run when tool errors."""
        self._handle_error(run_id, error)


def _wrap_configure(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_configure(*args, **kwargs):
        # type: (Any, Any) -> Any

        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)

        with capture_internal_exceptions():
            new_callbacks = []  # type: List[BaseCallbackHandler]
            if "local_callbacks" in kwargs:
                existing_callbacks = kwargs["local_callbacks"]
                kwargs["local_callbacks"] = new_callbacks
            elif len(args) > 2:
                existing_callbacks = args[2]
                args = (
                    args[0],
                    args[1],
                    new_callbacks,
                ) + args[3:]
            else:
                existing_callbacks = []

            if existing_callbacks:
                if isinstance(existing_callbacks, list):
                    for cb in existing_callbacks:
                        new_callbacks.append(cb)
                elif isinstance(existing_callbacks, BaseCallbackHandler):
                    new_callbacks.append(existing_callbacks)
                else:
                    logger.warn("Unknown callback type: %s", existing_callbacks)

            already_added = False
            for callback in new_callbacks:
                if isinstance(callback, SentryLangchainCallback):
                    already_added = True

            if not already_added:
                new_callbacks.append(
                    SentryLangchainCallback(
                        integration.max_spans, integration.include_prompts
                    )
                )
        return f(*args, **kwargs)

    return new_configure
