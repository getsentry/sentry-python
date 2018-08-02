from __future__ import absolute_import

from celery.signals import task_failure, task_prerun, task_postrun
from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk import get_current_hub, configure_scope, capture_exception
from sentry_sdk.hub import _internal_exceptions

from . import Integration


class CeleryIntegration(Integration):
    identifier = "celery"

    def __init__(self):
        pass

    def install(self, client):
        task_prerun.connect(self._handle_task_prerun, weak=False)
        task_postrun.connect(self._handle_task_postrun, weak=False)
        task_failure.connect(self._process_failure_signal, weak=False)

    def _process_failure_signal(self, sender, task_id, einfo, **kw):
        if hasattr(sender, "throws") and isinstance(einfo.exception, sender.throws):
            return

        if isinstance(einfo.exception, SoftTimeLimitExceeded):
            with get_current_hub().push_scope():
                with configure_scope() as scope:
                    scope.fingerprint = [
                        "celery",
                        "SoftTimeLimitExceeded",
                        getattr(sender, "name", sender),
                    ]

                capture_exception(einfo.exc_info)
        else:
            capture_exception(einfo.exc_info)

    def _handle_task_prerun(self, sender, task, **kw):
        with _internal_exceptions():
            get_current_hub().push_scope()

            with configure_scope() as scope:
                scope.transaction = task.name

    def _handle_task_postrun(self, sender, task_id, task, **kw):
        get_current_hub().pop_scope_unsafe()
