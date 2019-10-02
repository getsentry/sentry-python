from __future__ import absolute_import

import sys

from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.utils import (
    capture_internal_exceptions,
    exc_info_from_error,
    single_exception_from_error_tuple,
    walk_exception_chain,
    event_hint_with_exc_info,
)


def _capture_exception(exc_info, hub):
    from pyspark.taskcontext import TaskContext

    client = hub.client

    client_options = client.options
    mechanism = {"type": "spark", "handled": False}

    exc_info = exc_info_from_error(exc_info)

    exc_type, exc_value, tb = exc_info
    rv = []

    for exc_type, exc_value, tb in walk_exception_chain(exc_info):
        if exc_type not in (SystemExit, EOFError, ConnectionResetError):
            rv.append(
                single_exception_from_error_tuple(
                    exc_type, exc_value, tb, client_options, mechanism
                )
            )

    if rv:
        rv.reverse()
        hint = event_hint_with_exc_info(exc_info)
        event = {"level": "error", "exception": {"values": rv}}

        taskContext = TaskContext._getOrCreate()

        with configure_scope() as scope:
            scope.set_tag("stageId", taskContext.stageId())
            scope.set_tag("partitionId", taskContext.partitionId())
            scope.set_tag("attemptNumber", taskContext.attemptNumber())
            scope.set_tag("taskAttemptId", taskContext.taskAttemptId())
            scope.set_tag("stage_id", taskContext.stageId())
            scope.set_tag("stage_id", taskContext.stageId())
            scope.set_tag("app_name", taskContext._localProperties["app_name"])
            scope.set_tag(
                "application_id", taskContext._localProperties["application_id"]
            )
            scope.set_extra("callSite", taskContext._localProperties["callSite.short"])

        hub.capture_event(event, hint=hint)


def sentry_worker_main(*args, **kwargs):
    import pyspark.worker as original_worker

    try:
        original_worker.main(*args, **kwargs)
    except SystemExit:
        hub = Hub.current
        exc_info = sys.exc_info()
        with capture_internal_exceptions():
            _capture_exception(exc_info, hub)
