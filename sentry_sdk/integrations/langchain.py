from collections import OrderedDict
from functools import wraps

import sentry_sdk
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.consts import OP
from sentry_sdk.tracing import Span

if TYPE_CHECKING:
    from typing import Any, List, Callable, Dict, Union, Optional
    from uuid import UUID
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import logger, capture_internal_exceptions, event_from_exception

try:
    from langchain_core.agents import AgentAction, AgentFinish
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
        "The Sentry Python SDK requires 'tiktoken' in order to measure token usage from some Langchain APIs"
        "Please install 'tiktoken' if you aren't receiving token usage in Sentry."
        "See https://docs.sentry.io/platforms/python/integrations/langchain/ for more information."
    )

    def count_tokens(s):
        # type: (str) -> int
        return 0


class LangchainIntegration(Integration):
    identifier = "langchain"

    # The most number of spans (e.g., LLM calls) that can be processed at the same time.
    max_spans = 1024

    def __init__(self, include_prompts=False, max_spans=1024):
        # type: (LangchainIntegration, bool) -> None
        self.include_prompts = include_prompts
        self.max_spans = max_spans

    @staticmethod
    def setup_once():
        # type: () -> None
        manager._configure = _wrap_configure(manager._configure)


def _capture_exception(exc, type="langchain"):
    # type: (Any, str) -> None

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": type, "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


class WatchedSpan:
    span = None  # type: Span
    num_tokens = 0  # type: int

    def __init__(self, span, num_tokens=0):
        # type: (Span, int) -> None
        self.span = span
        self.num_tokens = num_tokens


class SentryLangchainCallback(BaseCallbackHandler):
    """Base callback handler that can be used to handle callbacks from langchain."""

    span_map = OrderedDict()  # type: OrderedDict[UUID, WatchedSpan]

    max_span_map_size = 0

    def __init__(self, max_span_map_size):
        self.max_span_map_size = max_span_map_size

    def gc_span_map(self):
        while len(self.span_map) > self.max_span_map_size:
            self.span_map.popitem(last=False)

    def on_llm_start(
        self,
        serialized,
        prompts,
        *,
        run_id,
        tags=None,
        parent_run_id=None,
        metadata=None,
        name=None,
        **kwargs,
    ):
        # type: (Dict[str, Any], List[str], *Any, UUID, Optional[List[str]], Optional[UUID], Optional[Dict[str, Any]], Optional[str], **Any) -> Any
        """Run when LLM starts running."""
        if not run_id:
            return
        span = sentry_sdk.start_span(
            op=OP.LANGCHAIN_INFERENCE, description="Langchain LLM call"
        )
        self.span_map[run_id] = WatchedSpan(span)
        self.gc_span_map()
        span.__enter__()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        # type: (Dict[str, Any], List[List[BaseMessage]], *Any, UUID, **Any) -> Any
        """Run when Chat Model starts running."""
        if not run_id:
            return
        span = sentry_sdk.start_span(
            op=OP.LANGCHAIN_INFERENCE, description="Langchain LLM call"
        )
        self.span_map[run_id] = WatchedSpan(span)
        self.gc_span_map()
        span.__enter__()

    def on_llm_new_token(self, token, *, run_id, **kwargs):
        # type: (str, *Any, UUID, **Any) -> Any
        """Run on new LLM token. Only available when streaming is enabled."""
        if not run_id or not self.span_map[run_id]:
            return
        span_data = self.span_map[run_id]
        if not span_data:
            return
        span_data.num_tokens += count_tokens(token)

    def on_llm_end(self, response, *, run_id, **kwargs):
        # type: (LLMResult, *Any, UUID, **Any) -> Any
        """Run when LLM ends running."""
        if not run_id:
            return

        span_data = self.span_map[run_id]
        if not span_data:
            return
        span_data.span.__exit__(None, None, None)

        print("llm end")

    def on_llm_error(self, error, *, run_id, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], *Any, UUID, **Any) -> Any
        """Run when LLM errors."""
        _capture_exception(error, "langchain-llm")

    def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        # type: (Dict[str, Any], Dict[str, Any], *Any, UUID, **Any) -> Any
        """Run when chain starts running."""
        if not run_id:
            return
        span = sentry_sdk.start_span(
            op=OP.LANGCHAIN_INFERENCE, description="Langchain chain execution"
        )
        self.span_map[run_id] = WatchedSpan(span)
        self.gc_span_map()
        span.__enter__()

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        # type: (Dict[str, Any], *Any, UUID, **Any) -> Any
        """Run when chain ends running."""
        if not run_id or not self.span_map[run_id]:
            return

        span_data = self.span_map[run_id]
        if not span_data:
            return
        span_data.span.__exit__(None, None, None)
        print("chain end")

    def on_chain_error(self, error, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], **Any) -> Any
        """Run when chain errors."""
        _capture_exception(error, "langchain-chain")

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        # type: (Dict[str, Any], str, *Any, UUID, **Any) -> Any
        """Run when tool starts running."""
        if not run_id:
            return
        print("tool_start")

    def on_tool_end(self, output, *, run_id, **kwargs):
        # type: (str, *Any, UUID, **Any) -> Any
        """Run when tool ends running."""
        if not run_id or not self.span_map[run_id]:
            return

        span_data = self.span_map[run_id]
        if not span_data:
            return
        span_data.span.__exit__(None, None, None)
        print("tool_end", output)

    def on_tool_error(self, error, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], **Any) -> Any
        """Run when tool errors."""
        _capture_exception(error, "langchain-tool")

    def on_agent_action(self, action, *, run_id, **kwargs):
        # type: (AgentAction, *Any, UUID, **Any) -> Any
        """Run on agent action."""
        if not run_id:
            return

    def on_agent_finish(self, finish, *, run_id, **kwargs):
        # type: (AgentFinish, *Any, UUID, **Any) -> Any
        """Run on agent end."""
        if not run_id:
            return


def _wrap_configure(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_configure(*args, **kwargs):
        # type: (*Any, **Any) -> Any

        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)

        with capture_internal_exceptions():
            new_callbacks = []
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
                new_callbacks.append(SentryLangchainCallback(integration.max_spans))
        return f(*args, **kwargs)

    return new_configure
