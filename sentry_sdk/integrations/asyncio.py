from __future__ import absolute_import

from sentry_sdk.consts import OP
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk._types import MYPY

try:
    import asyncio
    from asyncio.tasks import Task
except ImportError:
    raise DidNotEnable("asyncio not available")


if MYPY:
    from typing import Any


def _sentry_task_factory(loop, coro):
    # type: (Any, Any) -> Task[None]

    async def _coro_creating_hub_and_span():
        # type: () -> None
        hub = Hub(Hub.current)
        with hub:
            with hub.start_span(op=OP.FUNCTION, description=coro.__qualname__):
                await coro

    # Trying to use user set task factory (if there is one)
    orig_factory = loop.get_task_factory()
    if orig_factory:
        return orig_factory(loop, _coro_creating_hub_and_span)

    # The default task factory in `asyncio` does not have its own function
    # but is just a couple of lines in `asyncio.base_events.create_task()`
    # Those lines are copied here.

    # WARNING:
    # If the default behavior of the task creation in asyncio changes,
    # this will break!
    task = Task(_coro_creating_hub_and_span, loop=loop)  # type: ignore
    if task._source_traceback:  # type: ignore
        del task._source_traceback[-1]  # type: ignore

    return task


def patch_asyncio():
    # type: () -> None
    try:
        loop = asyncio.get_running_loop()
        loop.set_task_factory(_sentry_task_factory)
    except RuntimeError:
        # When there is no running loop, we have nothing to patch.
        pass


class AsyncioIntegration(Integration):
    identifier = "asyncio"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_asyncio()
