from __future__ import absolute_import

from celery.signals import task_failure, task_prerun, task_postrun
from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration


class CeleryIntegration(Integration):
    identifier = "celery"

    def __init__(self):
        pass

    def install(self):
        task_prerun.connect(self._handle_task_prerun, weak=False)
        task_postrun.connect(self._handle_task_postrun, weak=False)
        task_failure.connect(self._process_failure_signal, weak=False)

    def _process_failure_signal(self, sender, task_id, einfo, **kw):
        if hasattr(sender, "throws") and isinstance(einfo.exception, sender.throws):
            return

        hub = Hub.current
        if isinstance(einfo.exception, SoftTimeLimitExceeded):
            with hub.push_scope():
                with hub.configure_scope() as scope:
                    scope.fingerprint = [
                        "celery",
                        "SoftTimeLimitExceeded",
                        getattr(sender, "name", sender),
                    ]

                self._capture_event(hub, einfo.exc_info)
        else:
            self._capture_event(hub, einfo.exc_info)

    def _capture_event(self, hub, exc_info):
        event, hint = event_from_exception(
            exc_info,
            with_locals=hub.client.options["with_locals"],
            mechanism={"type": "celery", "handled": False},
        )

        hub.capture_event(event, hint=hint)

    def _handle_task_prerun(self, sender, task, **kw):
        with capture_internal_exceptions():
            hub = Hub.current
            hub.push_scope()
            with hub.configure_scope() as scope:
                scope.transaction = task.name

    def _handle_task_postrun(self, sender, task_id, task, **kw):
        Hub.current.pop_scope_unsafe()
