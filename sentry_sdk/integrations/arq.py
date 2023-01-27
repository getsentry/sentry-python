from __future__ import absolute_import

from sentry_sdk._types import MYPY
from sentry_sdk import Hub
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.integrations.logging import ignore_logger

try:
    from arq.version import VERSION as ARQ_VERSION
    from arq.connections import ArqRedis
    from arq.jobs import Job
    from arq.worker import JobExecutionFailed, Retry, RetryJob
except ImportError:
    raise DidNotEnable("Arq is not installed")

if MYPY:
    from typing import Any, Optional

ARQ_CONTROL_FLOW_EXCEPTIONS = (JobExecutionFailed, Retry, RetryJob)


class ArqIntegration(Integration):
    identifier = "arq"

    @staticmethod
    def setup_once():
        # type: () -> None

        try:
            if isinstance(ARQ_VERSION, str):
                version = tuple(map(int, ARQ_VERSION.split(".")[:2]))
            else:
                version = ARQ_VERSION.version[:2]
        except (TypeError, ValueError):
            raise DidNotEnable("arq version unparsable: {}".format(ARQ_VERSION))

        if version < (0, 23):
            raise DidNotEnable("arq 0.23 or newer required.")

        patch_enqueue_job()

        ignore_logger("arq.worker")


def patch_enqueue_job():
    # type: () -> None
    old_enqueue_job = ArqRedis.enqueue_job

    async def _sentry_enqueue_job(self, function, *args, **kwargs):
        # type: (ArqRedis, str, *Any, **Any) -> Optional[Job]
        hub = Hub.current

        if hub.get_integration(ArqIntegration) is None:
            return await old_enqueue_job(self, function, *args, **kwargs)

        with hub.start_span(op=OP.QUEUE_SUBMIT_ARQ, description=function):
            return await old_enqueue_job(self, function, *args, **kwargs)

    ArqRedis.enqueue_job = _sentry_enqueue_job
