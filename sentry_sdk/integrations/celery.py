from __future__ import absolute_import

from celery.signals import task_failure, task_prerun, task_postrun
from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration


class CeleryIntegration(Integration):
    identifier = "celery"

    @classmethod
    def install(cls):
        task_prerun.connect(cls._handle_task_prerun, weak=False)
        task_postrun.connect(cls._handle_task_postrun, weak=False)
        task_failure.connect(cls._process_failure_signal, weak=False)

    @classmethod
    def _process_failure_signal(cls, sender, task_id, einfo, **kw):
        hub = Hub.current
        integration = hub.get_integration(cls)
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
                _capture_event(hub, einfo.exc_info)
        else:
            _capture_event(hub, einfo.exc_info)

    @classmethod
    def _handle_task_prerun(cls, sender, task, **kw):
        hub = Hub.current
        integration = hub.get_integration(cls)
        if integration is None:
            return

        with hub.push_scope() as scope:
            with capture_internal_exceptions():
                scope.transaction = task.name

    @classmethod
    def _handle_task_postrun(cls, sender, task_id, task, **kw):
        hub = Hub.current
        integration = hub.get_integration(cls)
        if integration is not None:
            hub.pop_scope_unsafe()


def _capture_event(hub, exc_info):
    event, hint = event_from_exception(
        exc_info,
        with_locals=hub.client.options["with_locals"],
        mechanism={"type": "celery", "handled": False},
    )
    hub.capture_event(event, hint=hint)
