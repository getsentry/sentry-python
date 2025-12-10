from functools import wraps

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.tracing import SPANSTATUS

try:
    # django.tasks were added in Django 6.0
    from django.tasks.base import Task, TaskResultStatus
except ImportError:
    Task = None

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def patch_tasks():
    # type: () -> None
    if Task is None:
        return

    old_task_enqueue = Task.enqueue

    @wraps(old_task_enqueue)
    def _sentry_enqueue(self, *args, **kwargs):
        # type: (Any, Any, Any) -> Any
        from sentry_sdk.integrations.django import DjangoIntegration

        integration = sentry_sdk.get_client().get_integration(DjangoIntegration)
        if integration is None:
            return old_task_enqueue(self, *args, **kwargs)

        name = (
            getattr(self.func, "__name__", repr(self.func)) or "<unknown Django task>"
        )

        with sentry_sdk.start_span(
            op=OP.QUEUE_SUBMIT_DJANGO, name=name, origin=DjangoIntegration.origin
        ):
            return old_task_enqueue(self, *args, **kwargs)

    Task.enqueue = _sentry_enqueue
