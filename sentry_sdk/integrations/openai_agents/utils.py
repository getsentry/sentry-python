import sentry_sdk
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.openai_agents.run_hooks import SentryRunHooks
from sentry_sdk.utils import event_from_exception
from functools import wraps
import asyncio

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable

try:
    import agents
    from agents import RunHooks

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai_agents", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _get_start_span_function():
    # type: () -> Callable
    current_span = sentry_sdk.get_current_span()
    is_transaction = (
        current_span is not None and current_span.containing_transaction == current_span
    )
    return sentry_sdk.start_span if is_transaction else sentry_sdk.start_transaction


def _create_hook_wrapper(original_hook, sentry_hook):
    # type: (Callable, Callable) -> Callable
    @wraps(original_hook)
    async def async_wrapper(*args, **kwargs):
        await sentry_hook(*args, **kwargs)
        return await original_hook(*args, **kwargs)

    return async_wrapper


def _wrap_hooks(hooks):
    # type: (RunHooks) -> RunHooks
    """
    Our integration uses RunHooks to create spans. This function will either
    enable our SentryRunHooks or if the users has given custom RunHooks wrap
    them so the Sentry hooks and the users hooks are both called
    """
    sentry_hooks = SentryRunHooks()

    if hooks is None:
        return sentry_hooks

    wrapped_hooks = type("SentryWrappedHooks", (hooks.__class__,), {})

    # Wrap all methods from RunHooks
    for method_name in dir(RunHooks):
        if method_name.startswith("on_"):
            original_method = getattr(hooks, method_name)
            # Only wrap if the method exists in SentryRunHooks
            try:
                sentry_method = getattr(sentry_hooks, method_name)
                setattr(
                    wrapped_hooks,
                    method_name,
                    _create_hook_wrapper(original_method, sentry_method),
                )
            except AttributeError:
                # If method doesn't exist in SentryRunHooks, just use the original method
                setattr(wrapped_hooks, method_name, original_method)

    return wrapped_hooks()


def _create_run_wrapper(original_func):
    # type: (Callable) -> Callable
    """
    Wraps the run methods of the Runner class to create a root span for the agent runs.
    """
    is_async = asyncio.iscoroutinefunction(original_func)

    @classmethod
    @wraps(original_func)
    async def async_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with _get_start_span_function()(name=f"{agent.name} workflow"):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = await original_func(*args, **kwargs)
            return result

    @classmethod
    @wraps(original_func)
    def sync_wrapper(cls, *args, **kwargs):
        # type: (agents.Runner, *Any, **Any) -> Any
        agent = args[0]
        with _get_start_span_function()(name=f"{agent.name} workflow"):
            kwargs["hooks"] = _wrap_hooks(kwargs.get("hooks"))
            result = original_func(*args, **kwargs)
            return result

    return async_wrapper if is_async else sync_wrapper
