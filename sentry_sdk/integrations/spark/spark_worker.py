from __future__ import absolute_import

import sys

from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import (
    capture_internal_exceptions,
    exc_info_from_error,
    single_exception_from_error_tuple,
    walk_exception_chain,
    event_hint_with_exc_info,
)


class SparkWorkerIntegration(Integration):
    identifier = "spark_worker"

    @staticmethod
    def setup_once():
        # type: () -> None
        import pyspark.daemon as original_daemon

        original_daemon.worker_main = _sentry_worker_main


def _capture_exception(exc_info, hub):
    client = hub.client

    client_options = client.options
    mechanism = {"type": "spark", "handled": False}

    exc_info = exc_info_from_error(exc_info)

    exc_type, exc_value, tb = exc_info
    rv = []

    # On Exception worker will call sys.exit(-1), so we can ignore SystemExit and similar errors
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

        _tag_task_context()

        hub.capture_event(event, hint=hint)


def _tag_task_context():
    from pyspark.taskcontext import TaskContext

    with capture_internal_exceptions():
        with configure_scope() as scope:

            @scope.add_event_processor
            def process_event(event, hint):
                integration = Hub.current.get_integration(SparkWorkerIntegration)
                taskContext = TaskContext.get()

                if integration is None or taskContext is None:
                    return event

                event.setdefault("tags", {}).setdefault(
                    "stageId", taskContext.stageId()
                )
                event["tags"].setdefault("partitionId", taskContext.partitionId())
                event["tags"].setdefault("attemptNumber", taskContext.attemptNumber())
                event["tags"].setdefault("taskAttemptId", taskContext.taskAttemptId())

                if taskContext._localProperties:
                    if "sentry_app_name" in taskContext._localProperties:
                        event["tags"].setdefault(
                            "app_name", taskContext._localProperties["sentry_app_name"]
                        )
                        event["tags"].setdefault(
                            "application_id",
                            taskContext._localProperties["sentry_application_id"],
                        )

                    if "callSite.short" in taskContext._localProperties:
                        event.setdefault("extra", {}).setdefault(
                            "callSite", taskContext._localProperties["callSite.short"]
                        )

                return event


def _sentry_worker_main(*args, **kwargs):
    import pyspark.worker as original_worker

    try:
        original_worker.main(*args, **kwargs)
    except SystemExit:
        if Hub.current.get_integration(SparkWorkerIntegration) is not None:
            hub = Hub.current
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(exc_info, hub)
