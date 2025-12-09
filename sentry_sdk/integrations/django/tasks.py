from functools import wraps

import sentry_sdk
from sentry_sdk.consts import OP

try:
    # django.tasks were added in Django 6.0
    from django.tasks.base import Task
except ImportError:
    Task = None


def patch_tasks():
    # type: () -> None
    if Task is None:
        return

    old_task_enqueue = Task.enqueue

    @wraps(old_task_enqueue)
    def _sentry_enqueue(self, *args, **kwargs):
        # type: (Any, Any) -> Any
        from sentry_sdk.integrations.django import DjangoIntegration

        integration = sentry_sdk.get_client().get_integration(DjangoIntegration)
        if integration is None:
            return old_task_enqueue(*args, **kwargs)

        name = (
            getattr(self.func, "__name__", repr(self.func)) or "<unknown Django task>"
        )

        with sentry_sdk.start_span(
            op=OP.QUEUE_SUBMIT_DJANGO, name=name, origin=DjangoIntegration.origin
        ):
            return old_task_enqueue(*args, **kwargs)

    Task.enqueue = _sentry_enqueue
