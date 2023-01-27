from __future__ import absolute_import

import os

from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import add_global_event_processor

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional

    from sentry_sdk._types import Event, Hint

__all__ = ["CronMonitorIntegration"]


# The sentry-cli sets the monitor ID in the execution environment
monitor_id = os.environ.get("SENTRY_MONITOR_ID")
if monitor_id is None:
    raise DidNotEnable("SENTRY_MONITOR_ID is not set")


class CronMonitorIntegration(Integration):
    identifier = "cron_monitor"

    @staticmethod
    def setup_once():
        # type: () -> None
        @add_global_event_processor
        def processor(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            if Hub.current.get_integration(CronMonitorIntegration) is not None:
                ctx = event.setdefault("contexts", {}).setdefault("monitor", {})
                ctx["id"] = monitor_id

            return event
