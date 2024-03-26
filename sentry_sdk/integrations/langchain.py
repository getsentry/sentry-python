from functools import wraps

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, List, Callable, Dict, Union
from sentry_sdk.hub import Hub
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

    def __init__(self, include_prompts=False):
        # type: (LangchainIntegration, bool) -> None
        self.include_prompts = include_prompts

    @staticmethod
    def setup_once():
        # type: () -> None
        manager._configure = _wrap_configure(manager._configure)


def _capture_exception(hub, exc, type="langchain"):
    # type: (Hub, Any, str) -> None

    if hub.client is not None:
        event, hint = event_from_exception(
            exc,
            client_options=hub.client.options,
            mechanism={"type": type, "handled": False},
        )
        hub.capture_event(event, hint=hint)


# TODO types
class SentryLangchainCallback(BaseCallbackHandler):
    """Base callback handler that can be used to handle callbacks from langchain."""

    def on_llm_start(self, serialized, prompts, **kwargs):
        # type: (Dict[str, Any], List[str], **Any) -> Any
        """Run when LLM starts running."""
        print("on_llm_start")

    def on_chat_model_start(self, serialized, messages, **kwargs):
        # type: (Dict[str, Any], List[List[BaseMessage]], **Any) -> Any
        """Run when Chat Model starts running."""
        print("on_chat_model_start")

    def on_llm_new_token(self, token, **kwargs):
        # type: (str, **Any) -> Any
        """Run on new LLM token. Only available when streaming is enabled."""
        print("new token")

    def on_llm_end(self, response, **kwargs):
        # type: (LLMResult, **Any) -> Any
        """Run when LLM ends running."""
        print("llm end")

    def on_llm_error(self, error, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], **Any) -> Any
        """Run when LLM errors."""
        hub = Hub.current
        if hub:
            _capture_exception(hub, error, "langchain-llm")

    def on_chain_start(self, serialized, inputs, **kwargs):
        # type: (Dict[str, Any], Dict[str, Any], **kwargs) -> Any
        """Run when chain starts running."""
        print("chain start: ", serialized)

    def on_chain_end(self, outputs, **kwargs):
        # type: (Dict[str, Any], **Any) -> Any
        """Run when chain ends running."""
        print("chain end")

    def on_chain_error(self, error, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], **Any) -> Any
        """Run when chain errors."""
        hub = Hub.current
        if hub:
            _capture_exception(hub, error, "langchain-chain")

    def on_tool_start(self, serialized, input_str, **kwargs):
        # type: (Dict[str, Any], str, **Any) -> Any
        """Run when tool starts running."""
        print("tool_start")

    def on_tool_end(self, output, **kwargs):
        # type: (str, **Any) -> Any
        """Run when tool ends running."""
        print("tool_end", output)

    def on_tool_error(self, error, **kwargs):
        # type: (Union[Exception, KeyboardInterrupt], **Any) -> Any
        """Run when tool errors."""
        hub = Hub.current
        if hub:
            _capture_exception(hub, error, "langchain-tool")

    def on_text(self, text, **kwargs):
        # type: (str, Any) -> Any
        """Run on arbitrary text."""
        print("text: ", text)

    def on_agent_action(self, action, **kwargs):
        # type: (AgentAction, **Any) -> Any
        """Run on agent action."""
        print("agent_action", action)

    def on_agent_finish(self, finish, **kwargs):
        # type: (AgentFinish, **Any) -> Any
        """Run on agent end."""
        print("agent_finish", finish)


def _wrap_configure(f):
    # type: (Callable[..., Any]) -> Callable[..., Any]

    @wraps(f)
    def new_configure(*args, **kwargs):
        # type: (*Any, **Any) -> Any

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
                new_callbacks.append(SentryLangchainCallback())
        return f(*args, **kwargs)

    return new_configure
