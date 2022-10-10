from __future__ import absolute_import

import asyncio
from asyncio.tasks import Task


from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any


def _sentry_task_factory(loop, coro):
    # type: (Any, Any) -> Task

    async def _coro_creating_hub_and_span():
        hub = Hub(Hub.current)
        with hub:
            with hub.start_span(op="asyncprocess", description=coro.__qualname__):
                await coro

    # The default task factory in `asyncio`` does not have its own function
    # but is just a couple of lines in `asyncio.base_events.create_task()`
    # Those lines are copied here.

    # WARNING:
    # If the default behavior of the task creation in asyncio changes,
    # this will break!
    task = Task(_coro_creating_hub_and_span(), loop=loop)
    if task._source_traceback:
        del task._source_traceback[-1]

    return task


def patch_asyncio():
    # type: () -> None
    loop = asyncio.get_running_loop()
    loop.set_task_factory(_sentry_task_factory)


class AsyncioIntegration(Integration):
    identifier = "asyncio"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_asyncio()
