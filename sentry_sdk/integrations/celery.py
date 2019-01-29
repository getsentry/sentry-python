from __future__ import absolute_import

import sys

from celery.exceptions import SoftTimeLimitExceeded

from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk._compat import reraise
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import ignore_logger


class CeleryIntegration(Integration):
    identifier = "celery"

    @staticmethod
    def setup_once():
        import celery.app.trace as trace

        old_build_tracer = trace.build_tracer

        def sentry_build_tracer(name, task, *args, **kwargs):
            task.__call__ = _wrap_task_call(task, task.__call__)
            task.run = _wrap_task_call(task, task.run)
            return old_build_tracer(name, task, *args, **kwargs)

        trace.build_tracer = sentry_build_tracer

        # This logger logs every status of every task that ran on the worker.
        # Meaning that every task's breadcrumbs are full of stuff like "Task
        # <foo> raised unexpected <bar>".
        ignore_logger("celery.worker.job")


def _wrap_task_call(self, f):
    def _inner(*args, **kwargs):
        hub = Hub.current
        if hub.get_integration(CeleryIntegration) is None:
            return f(*args, **kwargs)

        with hub.configure_scope() as scope:
            if scope._name == "celery":
                return f(*args, **kwargs)

        with hub.push_scope() as scope:
            scope._name = "celery"
            scope.add_event_processor(_make_event_processor(args, kwargs, self))
            try:
                return f(*args, **kwargs)
            except Exception:
                reraise(*_capture_exception(hub))

    return _inner


def _make_event_processor(args, kwargs, task):
    def event_processor(event, hint):
        with capture_internal_exceptions():
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


def _capture_exception(hub):
    exc_info = sys.exc_info()
    event, hint = event_from_exception(
        exc_info,
        client_options=hub.client.options,
        mechanism={"type": "celery", "handled": False},
    )
    hub.capture_event(event, hint=hint)
    return exc_info
