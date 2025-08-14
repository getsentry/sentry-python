from __future__ import annotations
import sys

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.utils import event_from_exception, logger, reraise
from sentry_sdk.transport import AsyncHttpTransport

try:
    import asyncio
    from asyncio.tasks import Task
except ImportError:
    raise DidNotEnable("asyncio not available")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from collections.abc import Coroutine

    from sentry_sdk._types import ExcInfo


def get_name(coro: Any) -> str:
    return (
        getattr(coro, "__qualname__", None)
        or getattr(coro, "__name__", None)
        or "coroutine without __name__"
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
        if not client:
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
        finally:
            orig_close()

    loop.close = _patched_close  # type: ignore
    loop._sentry_flush_patched = True  # type: ignore


def patch_asyncio() -> None:
    orig_task_factory = None
    try:
        loop = asyncio.get_running_loop()
        orig_task_factory = loop.get_task_factory()

        def _sentry_task_factory(
            loop: asyncio.AbstractEventLoop,
            coro: Coroutine[Any, Any, Any],
            **kwargs: Any,
        ) -> asyncio.Future[Any]:

            async def _task_with_sentry_span_creation() -> Any:
                result = None

                with sentry_sdk.isolation_scope():
                    with sentry_sdk.start_span(
                        op=OP.FUNCTION,
                        name=get_name(coro),
                        origin=AsyncioIntegration.origin,
                        only_as_child_span=True,
                    ):
                        try:
                            result = await coro
                        except Exception:
                            reraise(*_capture_exception())

                return result

            task = None

            # Trying to use user set task factory (if there is one)
            if orig_task_factory:
                task = orig_task_factory(
                    loop, _task_with_sentry_span_creation(), **kwargs
                )

            if task is None:
                # The default task factory in `asyncio` does not have its own function
                # but is just a couple of lines in `asyncio.base_events.create_task()`
                # Those lines are copied here.

                # WARNING:
                # If the default behavior of the task creation in asyncio changes,
                # this will break!
                task = Task(_task_with_sentry_span_creation(), loop=loop, **kwargs)
                if task._source_traceback:  # type: ignore
                    del task._source_traceback[-1]  # type: ignore

            # Set the task name to include the original coroutine's name
            try:
                if isinstance(task, asyncio.Task):
                    task.set_name(f"{get_name(coro)} (Sentry-wrapped)")
            except AttributeError:
                # set_name might not be available in all Python versions
                pass

            return task

        loop.set_task_factory(_sentry_task_factory)  # type: ignore

    except RuntimeError:
        # When there is no running loop, we have nothing to patch.
        logger.warning(
            "There is no running asyncio loop so there is nothing Sentry can patch. "
            "Please make sure you call sentry_sdk.init() within a running "
            "asyncio loop for the AsyncioIntegration to work. "
            "See https://docs.sentry.io/platforms/python/integrations/asyncio/"
        )


def _capture_exception() -> ExcInfo:
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

    @staticmethod
    def setup_once() -> None:
        patch_asyncio()
        patch_loop_close()
