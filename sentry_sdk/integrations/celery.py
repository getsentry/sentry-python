from __future__ import absolute_import

import sys

from celery.signals import task_failure, task_prerun, task_postrun
from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger


class CeleryIntegration(Integration):
    identifier = "celery"

    @staticmethod
    def setup_once():
        task_prerun.connect(_handle_task_prerun, weak=False)
        task_postrun.connect(_handle_task_postrun, weak=False)
        task_failure.connect(_process_failure_signal, weak=False)

        # This logger logs every status of every task that ran on the worker.
        # Meaning that every task's breadcrumbs are full of stuff like "Task
        # <foo> raised unexpected <bar>".
        ignore_logger("celery.worker.job")


def _process_failure_signal(sender, task_id, einfo, **kw):
    # einfo from celery is not reliable
    exc_info = sys.exc_info()

    hub = Hub.current
    integration = hub.get_integration(CeleryIntegration)
    if integration is None:
        return

    _capture_event(hub, exc_info)


def _handle_task_prerun(sender, task, args, kwargs, **_):
    hub = Hub.current
    if hub.get_integration(CeleryIntegration) is not None:
        scope = hub.push_scope().__enter__()
        scope.add_event_processor(_make_event_processor(args, kwargs, task))


def _make_event_processor(args, kwargs, task):
    def event_processor(event, hint):
        with capture_internal_exceptions():
            if "transaction" not in event:
                event["transaction"] = task.name

        with capture_internal_exceptions():
            extra = event.setdefault("extra", {})
            extra["celery-job"] = {
                "task_name": task.name,
                "args": args,
                "kwargs": kwargs,
            }

        if "exc_info" in hint:
            with capture_internal_exceptions():
                if issubclass(hint["exc_info"][0], SoftTimeLimitExceeded):
                    event["fingerprint"] = [
                        "celery",
                        "SoftTimeLimitExceeded",
                        getattr(task, "name", task),
                    ]

            with capture_internal_exceptions():
                if hasattr(task, "throws") and isinstance(
                    hint["exc_info"][1], task.throws
                ):
                    return None

        return event

    return event_processor


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
