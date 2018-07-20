from __future__ import absolute_import

from celery.signals import (
    after_setup_logger, task_failure, task_prerun, task_postrun
)

from sentry_sdk import get_current_hub, configure_scope, capture_exception
from sentry_sdk.utils import ContextVar


def install():
    task_prerun.connect(_handle_task_prerun, weak=False)
    task_postrun.connect(_handle_task_postrun, weak=False)
    task_failure.connect(_process_failure_signal, weak=False)


def _process_failure_signal(sender, task_id, einfo, **kw):
    if hasattr(sender, 'throws') and isinstance(einfo.exception, sender.throws):
        return

    capture_exception(einfo.exc_info)


_task_scope = ContextVar('sentry_celery_task_scope')


def _handle_task_prerun(sender, task, **kw):
    try:
        assert _task_scope.get(None) is None, 'race condition'
        _task_scope.set(get_current_hub().push_scope().__enter__())

        with configure_scope() as scope:
            scope.transaction = task.name
    except Exception:
        get_current_hub().capture_internal_exception()


def _handle_task_postrun(sender, task_id, task, **kw):
    try:
        val = _task_scope.get(None)
        assert val is not None, 'race condition'
        val.__exit__(None, None, None)
        _task_scope.set(None)
    except Exception:
        get_current_hub().capture_internal_exception()
