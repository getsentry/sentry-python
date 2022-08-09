from datetime import datetime

from sentry_sdk._types import MYPY
from sentry_sdk import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing import Transaction, TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import capture_internal_exceptions

if MYPY:
    from typing import Any, Optional

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

            transaction = Transaction(
                name=task.name,
                status="ok",
                op="huey.task",
                source=TRANSACTION_SOURCE_TASK,
            )

            with hub.start_transaction(transaction):
                return old_execute(self, task, timestamp)

    Huey._execute = _sentry_execute
