import functools
import sys

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations._wsgi_common import nullcontext
from sentry_sdk.traces import StreamedSpan
from sentry_sdk.tracing import Span
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.transport import AsyncHttpTransport
from sentry_sdk.utils import (
    event_from_exception,
    is_internal_task,
    logger,
    reraise,
)

try:
    import asyncio
    from asyncio.tasks import Task
except ImportError:
    raise DidNotEnable("asyncio not available")

from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any, Callable, TypeVar

    from sentry_sdk._types import ExcInfo

    T = TypeVar("T", bound=Callable[..., Any])


def get_name(coro: "Any") -> str:
    return (
        getattr(coro, "__qualname__", None)
        or getattr(coro, "__name__", None)
        or "coroutine without __name__"
    )


def _wrap_coroutine(wrapped: "Coroutine[Any, Any, Any]") -> "Callable[[T], T]":
    # Only __name__ and __qualname__ are copied from function to coroutine in CPython
    return functools.partial(
        functools.update_wrapper,
        wrapped=wrapped,  # type: ignore
        assigned=("__name__", "__qualname__"),
        updated=(),
    )


def patch_loop_close() -> None:
    """Patch loop.close to flush pending events before shutdown."""
    # Atexit shutdown hook happens after the event loop is closed.
    # Therefore, it is necessary to patch the loop.close method to ensure
    # that pending events are flushed before the interpreter shuts down.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop → cannot patch now
        return

    if getattr(loop, "_sentry_flush_patched", False):
        return

    async def _flush() -> None:
        client = sentry_sdk.get_client()
        if not client.is_active():
            return

        try:
            if not isinstance(client.transport, AsyncHttpTransport):
                return

            await client.close_async()
        except Exception:
            logger.warning("Sentry flush failed during loop shutdown", exc_info=True)

    orig_close = loop.close

    def _patched_close() -> None:
        try:
            loop.run_until_complete(_flush())
        except Exception:
            logger.debug(
                "Could not flush Sentry events during loop close", exc_info=True
            )
        finally:
            orig_close()

    loop.close = _patched_close  # type: ignore
    loop._sentry_flush_patched = True  # type: ignore


def _create_task_with_factory(
    orig_task_factory: "Any",
    loop: "asyncio.AbstractEventLoop",
    coro: "Coroutine[Any, Any, Any]",
    **kwargs: "Any",
) -> "asyncio.Task[Any]":
    task = None

    # Trying to use user set task factory (if there is one)
    if orig_task_factory:
        task = orig_task_factory(loop, coro, **kwargs)

    if task is None:
        # The default task factory in `asyncio` does not have its own function
        # but is just a couple of lines in `asyncio.base_events.create_task()`
        # Those lines are copied here.

        # WARNING:
        # If the default behavior of the task creation in asyncio changes,
        # this will break!
        task = Task(coro, loop=loop, **kwargs)
        if task._source_traceback:  # type: ignore
            del task._source_traceback[-1]  # type: ignore

    return task


def patch_asyncio() -> None:
    orig_task_factory = None
    try:
        loop = asyncio.get_running_loop()
        orig_task_factory = loop.get_task_factory()

        # Check if already patched
        if getattr(orig_task_factory, "_is_sentry_task_factory", False):
            return

        def _sentry_task_factory(
            loop: "asyncio.AbstractEventLoop",
            coro: "Coroutine[Any, Any, Any]",
            **kwargs: "Any",
        ) -> "asyncio.Future[Any]":
            # Check if this is an internal Sentry task
            if is_internal_task():
                return _create_task_with_factory(
                    orig_task_factory, loop, coro, **kwargs
                )

            @_wrap_coroutine(coro)
            async def _task_with_sentry_span_creation() -> "Any":
                result = None
                client = sentry_sdk.get_client()
                integration = client.get_integration(AsyncioIntegration)
                task_spans = integration.task_spans if integration else False

                span_ctx: "Optional[Union[StreamedSpan, Span]]" = None
                is_span_streaming_enabled = has_span_streaming_enabled(client.options)

                with sentry_sdk.isolation_scope():
                    if task_spans:
                        if is_span_streaming_enabled:
                            span_ctx = sentry_sdk.traces.start_span(
                                name=get_name(coro),
                                attributes={
                                    "sentry.op": OP.FUNCTION,
                                    "sentry.origin": AsyncioIntegration.origin,
                                },
                            )
                        else:
                            span_ctx = sentry_sdk.start_span(
                                op=OP.FUNCTION,
                                name=get_name(coro),
                                origin=AsyncioIntegration.origin,
                            )

                    with span_ctx if span_ctx else nullcontext():
                        try:
                            result = await coro
                        except StopAsyncIteration as e:
                            raise e from None
                        except Exception:
                            reraise(*_capture_exception())

                return result

            task = _create_task_with_factory(
                orig_task_factory, loop, _task_with_sentry_span_creation(), **kwargs
            )

            # Set the task name to include the original coroutine's name
            try:
                task.set_name(f"{get_name(coro)} (Sentry-wrapped)")
            except AttributeError:
                # set_name might not be available in all Python versions
                pass

            return task

        _sentry_task_factory._is_sentry_task_factory = True  # type: ignore
        loop.set_task_factory(_sentry_task_factory)  # type: ignore

    except RuntimeError:
        # When there is no running loop, we have nothing to patch.
        logger.warning(
            "There is no running asyncio loop so there is nothing Sentry can patch. "
            "Please make sure you call sentry_sdk.init() within a running "
            "asyncio loop for the AsyncioIntegration to work. "
            "See https://docs.sentry.io/platforms/python/integrations/asyncio/"
        )


def _capture_exception() -> "ExcInfo":
    exc_info = sys.exc_info()

    client = sentry_sdk.get_client()

    integration = client.get_integration(AsyncioIntegration)
    if integration is not None:
        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "asyncio", "handled": False},
        )
        sentry_sdk.capture_event(event, hint=hint)

    return exc_info


class AsyncioIntegration(Integration):
    identifier = "asyncio"
    origin = f"auto.function.{identifier}"

    def __init__(self, task_spans: bool = True) -> None:
        self.task_spans = task_spans

    @staticmethod
    def setup_once() -> None:
        patch_asyncio()
        patch_loop_close()


def enable_asyncio_integration(*args: "Any", **kwargs: "Any") -> None:
    """
    Enable AsyncioIntegration with the provided options.

    This is useful in scenarios where Sentry needs to be initialized before
    an event loop is set up, but you still want to instrument asyncio once there
    is an event loop. In that case, you can sentry_sdk.init() early on without
    the AsyncioIntegration and then, once the event loop has been set up,
    execute:

    ```python
    from sentry_sdk.integrations.asyncio import enable_asyncio_integration

    async def async_entrypoint():
        enable_asyncio_integration()
    ```

    Any arguments provided will be passed to AsyncioIntegration() as is.

    If AsyncioIntegration has already patched the current event loop, this
    function won't have any effect.

    If AsyncioIntegration was provided in
    sentry_sdk.init(disabled_integrations=[...]), this function will ignore that
    and the integration will be enabled.
    """
    client = sentry_sdk.get_client()
    if not client.is_active():
        return

    # This function purposefully bypasses the integration machinery in
    # integrations/__init__.py. _installed_integrations/_processed_integrations
    # is used to prevent double patching the same module, but in the case of
    # the AsyncioIntegration, we don't monkeypatch the standard library directly,
    # we patch the currently running event loop, and we keep the record of doing
    # that on the loop itself.
    logger.debug("Setting up integration asyncio")

    integration = AsyncioIntegration(*args, **kwargs)
    integration.setup_once()

    if "asyncio" not in client.integrations:
        client.integrations["asyncio"] = integration
