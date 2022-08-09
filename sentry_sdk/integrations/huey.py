from datetime import datetime

from sentry_sdk._types import MYPY
from sentry_sdk import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing import Transaction, TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import capture_internal_exceptions

if MYPY:
    from typing import Any, Optional
    from sentry_sdk._types import EventProcessor, Event, Hint

try:
    from huey.api import Huey, Task
except ImportError:
    raise DidNotEnable("Huey is not installed")


class HueyIntegration(Integration):
    identifier = "huey"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_execute()


def _make_event_processor(task):
    # type: (Any) -> EventProcessor
    def event_processor(event, hint):
        # type: (Event, Hint) -> Optional[Event]

        with capture_internal_exceptions():
            tags = event.setdefault("tags", {})
            tags["huey_task_id"] = task.id
            tags["huey_task_retry"] = task.default_retries > task.retries
            extra = event.setdefault("extra", {})
            extra["huey-job"] = {
                "task": task.name,
                "args": task.args,
                "kwargs": task.kwargs,
                "retry": (task.default_retries or 0) - task.retries,
            }

        return event

    return event_processor


def patch_execute():
    # type: () -> None
    old_execute = Huey._execute

    def _sentry_execute(self, task, timestamp=None):
        # type: (Huey, Task, Optional[datetime]) -> Any
        hub = Hub.current

        if hub.get_integration(HueyIntegration) is None:
            return old_execute(self, task, timestamp)

        with hub.push_scope() as scope:
            with capture_internal_exceptions():
                scope._name = "huey"
                scope.clear_breadcrumbs()
                scope.add_event_processor(_make_event_processor(task))

            transaction = Transaction(
                name=task.name,
                status="ok",
                op="huey.task",
                source=TRANSACTION_SOURCE_TASK,
            )

            with hub.start_transaction(transaction):
                return old_execute(self, task, timestamp)

    Huey._execute = _sentry_execute
