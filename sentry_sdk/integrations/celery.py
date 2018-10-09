from __future__ import absolute_import

import sys

from celery.signals import task_failure, task_prerun, task_postrun
from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration


class CeleryIntegration(Integration):
    identifier = "celery"

    @staticmethod
    def setup_once():
        task_prerun.connect(_handle_task_prerun, weak=False)
        task_postrun.connect(_handle_task_postrun, weak=False)
        task_failure.connect(_process_failure_signal, weak=False)


def _process_failure_signal(sender, task_id, einfo, **kw):
    # einfo from celery is not reliable
    exc_info = sys.exc_info()

    hub = Hub.current
    integration = hub.get_integration(CeleryIntegration)
    if integration is None:
        return

    if hasattr(sender, "throws") and isinstance(einfo.exception, sender.throws):
        return

    if isinstance(einfo.exception, SoftTimeLimitExceeded):
        with hub.push_scope() as scope:
            scope.fingerprint = [
                "celery",
                "SoftTimeLimitExceeded",
                getattr(sender, "name", sender),
            ]
            _capture_event(hub, exc_info)
    else:
        _capture_event(hub, exc_info)


def _handle_task_prerun(sender, task, **kw):
    hub = Hub.current
    if hub.get_integration(CeleryIntegration) is not None:
        scope = hub.push_scope().__enter__()
        with capture_internal_exceptions():
            scope.transaction = task.name


def _handle_task_postrun(sender, task_id, task, **kw):
    hub = Hub.current
    if hub.get_integration(CeleryIntegration) is not None:
        hub.pop_scope_unsafe()


def _capture_event(hub, exc_info):
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "celery", "handled": False},
    )
    hub.capture_event(event, hint=hint)
