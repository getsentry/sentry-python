from __future__ import absolute_import

from threading import Lock

from celery.signals import task_failure, task_prerun, task_postrun

from sentry_sdk import get_current_hub, configure_scope, capture_exception


_installer_lock = Lock()
_installed = False


def install(client):
    global _installed
    with _installer_lock:
        if _installed:
            return

        task_prerun.connect(_handle_task_prerun, weak=False)
        task_postrun.connect(_handle_task_postrun, weak=False)
        task_failure.connect(_process_failure_signal, weak=False)

        _installed = True


def _process_failure_signal(sender, task_id, einfo, **kw):
    if hasattr(sender, "throws") and isinstance(einfo.exception, sender.throws):
        return

    capture_exception(einfo.exc_info)


def _handle_task_prerun(sender, task, **kw):
    try:
        get_current_hub().push_scope()

        with configure_scope() as scope:
            scope.transaction = task.name
    except Exception:
        get_current_hub().capture_internal_exception()


def _handle_task_postrun(sender, task_id, task, **kw):
    get_current_hub().pop_scope_unsafe()
