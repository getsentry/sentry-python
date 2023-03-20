from __future__ import absolute_import
import datetime
import functools
import sys
import tempfile
import shutil

from sentry_sdk.consts import OP
from sentry_sdk.crons import capture_checkin, MonitorStatus
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.tracing import Transaction, TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    nanosecond_time,
    logger,
)
from sentry_sdk._compat import reraise
from sentry_sdk._functools import wraps
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional
    from typing import TypeVar

    from sentry_sdk._types import EventProcessor, Event, Hint, ExcInfo

    from celery import Celery

    F = TypeVar("F", bound=Callable[..., Any])


try:
    import celery  # type: ignore
    import celery.beat  # type: ignore
    from celery import VERSION as CELERY_VERSION
    from celery.app.trace import task_has_custom
    from celery.schedules import crontab, schedule, solar  # type: ignore
    from celery.signals import (  # type: ignore
        beat_init,
        task_prerun,
        task_failure,
        task_success,
        task_retry,
    )
    from celery.exceptions import (  # type: ignore
        SoftTimeLimitExceeded,
        Retry,
        Ignore,
        Reject,
    )
except ImportError:
    raise DidNotEnable("Celery not installed")


CELERY_CONTROL_FLOW_EXCEPTIONS = (Retry, Ignore, Reject)


class CeleryIntegration(Integration):
    identifier = "celery"

    def __init__(self, propagate_traces=True, celery_app=None):
        # type: (bool, Optional[Celery]) -> None
        self.propagate_traces = propagate_traces
        self.celery_app = celery_app

        _instrument_celery_beat_tasks(celery_app)

    @staticmethod
    def setup_once():
        # type: () -> None
        if CELERY_VERSION < (3,):
            raise DidNotEnable("Celery 3 or newer required.")

        import celery.app.trace as trace  # type: ignore

        old_build_tracer = trace.build_tracer

        def sentry_build_tracer(name, task, *args, **kwargs):
            # type: (Any, Any, *Any, **Any) -> Any
            if not getattr(task, "_sentry_is_patched", False):
                # determine whether Celery will use __call__ or run and patch
                # accordingly
                if task_has_custom(task, "__call__"):
                    type(task).__call__ = _wrap_task_call(task, type(task).__call__)
                else:
                    task.run = _wrap_task_call(task, task.run)

                # `build_tracer` is apparently called for every task
                # invocation. Can't wrap every celery task for every invocation
                # or we will get infinitely nested wrapper functions.
                task._sentry_is_patched = True

            return _wrap_tracer(task, old_build_tracer(name, task, *args, **kwargs))

        trace.build_tracer = sentry_build_tracer

        from celery.app.task import Task  # type: ignore

        Task.apply_async = _wrap_apply_async(Task.apply_async)

        _patch_worker_exit()

        # This logger logs every status of every task that ran on the worker.
        # Meaning that every task's breadcrumbs are full of stuff like "Task
        # <foo> raised unexpected <bar>".
        ignore_logger("celery.worker.job")
        ignore_logger("celery.app.trace")

        # This is stdout/err redirected to a logger, can't deal with this
        # (need event_level=logging.WARN to reproduce)
        ignore_logger("celery.redirected")


def _wrap_apply_async(f):
    # type: (F) -> F
    @wraps(f)
    def apply_async(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        hub = Hub.current
        integration = hub.get_integration(CeleryIntegration)
        if integration is not None and integration.propagate_traces:
            with hub.start_span(
                op=OP.QUEUE_SUBMIT_CELERY, description=args[0].name
            ) as span:
                with capture_internal_exceptions():
                    headers = dict(hub.iter_trace_propagation_headers(span))

                    if headers:
                        # Note: kwargs can contain headers=None, so no setdefault!
                        # Unsure which backend though.
                        kwarg_headers = kwargs.get("headers") or {}
                        kwarg_headers.update(headers)

                        # https://github.com/celery/celery/issues/4875
                        #
                        # Need to setdefault the inner headers too since other
                        # tracing tools (dd-trace-py) also employ this exact
                        # workaround and we don't want to break them.
                        kwarg_headers.setdefault("headers", {}).update(headers)
                        kwargs["headers"] = kwarg_headers

                return f(*args, **kwargs)
        else:
            return f(*args, **kwargs)

    return apply_async  # type: ignore


def _wrap_tracer(task, f):
    # type: (Any, F) -> F

    # Need to wrap tracer for pushing the scope before prerun is sent, and
    # popping it after postrun is sent.
    #
    # This is the reason we don't use signals for hooking in the first place.
    # Also because in Celery 3, signal dispatch returns early if one handler
    # crashes.
    @wraps(f)
    def _inner(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        hub = Hub.current
        if hub.get_integration(CeleryIntegration) is None:
            return f(*args, **kwargs)

        with hub.push_scope() as scope:
            scope._name = "celery"
            scope.clear_breadcrumbs()
            scope.add_event_processor(_make_event_processor(task, *args, **kwargs))

            transaction = None

            # Celery task objects are not a thing to be trusted. Even
            # something such as attribute access can fail.
            with capture_internal_exceptions():
                transaction = Transaction.continue_from_headers(
                    args[3].get("headers") or {},
                    op=OP.QUEUE_TASK_CELERY,
                    name="unknown celery task",
                    source=TRANSACTION_SOURCE_TASK,
                )
                transaction.name = task.name
                transaction.set_status("ok")

            if transaction is None:
                return f(*args, **kwargs)

            with hub.start_transaction(
                transaction,
                custom_sampling_context={
                    "celery_job": {
                        "task": task.name,
                        # for some reason, args[1] is a list if non-empty but a
                        # tuple if empty
                        "args": list(args[1]),
                        "kwargs": args[2],
                    }
                },
            ):
                return f(*args, **kwargs)

    return _inner  # type: ignore


def _wrap_task_call(task, f):
    # type: (Any, F) -> F

    # Need to wrap task call because the exception is caught before we get to
    # see it. Also celery's reported stacktrace is untrustworthy.

    # functools.wraps is important here because celery-once looks at this
    # method's name.
    # https://github.com/getsentry/sentry-python/issues/421
    @wraps(f)
    def _inner(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        try:
            return f(*args, **kwargs)
        except Exception:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(task, exc_info)
            reraise(*exc_info)

    return _inner  # type: ignore


def _make_event_processor(task, uuid, args, kwargs, request=None):
    # type: (Any, Any, Any, Any, Optional[Any]) -> EventProcessor
    def event_processor(event, hint):
        # type: (Event, Hint) -> Optional[Event]

        with capture_internal_exceptions():
            tags = event.setdefault("tags", {})
            tags["celery_task_id"] = uuid
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

        return event

    return event_processor


def _capture_exception(task, exc_info):
    # type: (Any, ExcInfo) -> None
    hub = Hub.current

    if hub.get_integration(CeleryIntegration) is None:
        return
    if isinstance(exc_info[1], CELERY_CONTROL_FLOW_EXCEPTIONS):
        # ??? Doesn't map to anything
        _set_status(hub, "aborted")
        return

    _set_status(hub, "internal_error")

    if hasattr(task, "throws") and isinstance(exc_info[1], task.throws):
        return

    # If an integration is there, a client has to be there.
    client = hub.client  # type: Any

    event, hint = event_from_exception(
        exc_info,
        client_options=client.options,
        mechanism={"type": "celery", "handled": False},
    )

    hub.capture_event(event, hint=hint)


def _set_status(hub, status):
    # type: (Hub, str) -> None
    with capture_internal_exceptions():
        with hub.configure_scope() as scope:
            if scope.span is not None:
                scope.span.set_status(status)


def _patch_worker_exit():
    # type: () -> None

    # Need to flush queue before worker shutdown because a crashing worker will
    # call os._exit
    from billiard.pool import Worker  # type: ignore

    old_workloop = Worker.workloop

    def sentry_workloop(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        try:
            return old_workloop(*args, **kwargs)
        finally:
            with capture_internal_exceptions():
                hub = Hub.current
                if hub.get_integration(CeleryIntegration) is not None:
                    hub.flush()

    Worker.workloop = sentry_workloop


# For the signals to properly register, they need to be top-level objects.
# Since they are defined dynamically in _instrument_celery_beat_tasks(),
# we have to declare them up top, make them global, and override them.
celerybeat_startup = None
celery_task_before_run = None
celery_task_success = None
celery_task_failure = None
celery_task_retry = None


def _get_headers_from_task(task):
    # type: (celery.Task) -> Dict[str, Any]
    headers = task.request.headers or {}
    headers.update(task.request.get("properties", {}).get("application_headers", {}))
    return headers


def _instrument_celery_beat_tasks(celery_app):
    # type: (Celery) -> None

    global celerybeat_startup
    global celery_task_before_run
    global celery_task_success
    global celery_task_failure
    global celery_task_retry

    def celerybeat_startup(sender, **kwargs):
        # type: (celery.beat.Service, Dict[Any, Any]) -> None
        # This code is based on code in
        # https://github.com/cronitorio/cronitor-python/blob/master/cronitor/celery.py

        # To avoid recursion, since restarting celerybeat will result in this
        # signal being called again, we disconnect the signal.
        beat_init.disconnect(celerybeat_startup, dispatch_uid=1)

        # Must use the cached_property from scheduler so as not to re-open the shelve database
        scheduler = sender.scheduler  # type: celery.beat.Scheduler
        # Also need to use the property here, including for django-celery-beat
        schedules = scheduler.schedule

        add_periodic_task_instrumented = []
        for name in schedules:
            if name.startswith("celery."):
                continue

            schedule_entry = schedules[name]  # type: celery.beat.ScheduleEntry
            celery_schedule = schedule_entry.schedule  # type: celery.schedules.schedule

            if isinstance(celery_schedule, crontab):
                formatted_schedule = (
                    "{0._orig_minute} {0._orig_hour} {0._orig_day_of_week} {0._orig_day_of_month} "
                    "{0._orig_month_of_year}"
                ).format(celery_schedule)
                schedule_type = "cron"

            elif isinstance(celery_schedule, schedule):
                freq = celery_schedule.run_every  # type: datetime.timedelta
                formatted_schedule = str(freq.total_seconds())
                schedule_type = "interval"

            elif isinstance(celery_schedule, solar):
                # We don't support solar schedules
                logger.warning(
                    "The CeleryIntegration does not support "
                    "tasks using solar schedules. Task schedule '{}' will "
                    "not be monitored".format(name)
                )
                continue

            else:
                logger.warning(
                    "The CeleryIntegration does not support "
                    "schedules of type `{}`".format(type(celery_schedule))
                )
                continue

            headers = schedule_entry.options.pop("headers", {})
            headers.update(
                {
                    "headers": {
                        "sentry-celerybeat-name": name,
                        "sentry-celerybeat-schedule": formatted_schedule,
                        "sentry-celerybeat-schedule-type": schedule_type,
                    },
                }
            )

            add_periodic_task_instrumented.append(
                functools.partial(
                    celery_app.add_periodic_task,
                    schedule_entry.schedule,
                    # Setting headers in the signature
                    # works better than in periodic task options
                    celery_app.tasks.get(schedule_entry.task).s().set(headers=headers),
                    args=schedule_entry.args,
                    kwargs=schedule_entry.kwargs,
                    name=schedule_entry.name,
                    **(schedule_entry.options or {})
                )
            )

        if isinstance(sender.scheduler, celery.beat.PersistentScheduler):
            # The celerybeat-schedule file with shelve gets corrupted really easily, so we need
            # to set up a tempfile instead.
            new_schedule = tempfile.NamedTemporaryFile()
            with open(sender.schedule_filename, "rb") as current_schedule:
                shutil.copyfileobj(current_schedule, new_schedule)

            # We need to stop and restart celerybeat to get the task updates in place.
            # This isn't ideal, but seems to work.
            sender.stop()

            # Now, actually add all the periodic tasks to overwrite beat with the headers
            for task in add_periodic_task_instrumented:
                task()

            # Then, restart celerybeat, on the new schedule file (copied from the old one)
            celery_app.Beat(schedule=new_schedule.name).run()

        else:
            # For django-celery, etc., we don't need to stop and restart celerybeat
            for task in add_periodic_task_instrumented:
                task()

    beat_init.connect(celerybeat_startup, dispatch_uid=1)

    @task_prerun.connect  # type: ignore
    def celery_task_before_run(sender, **kwargs):
        # type: (celery.Task, Dict[Any, Any]) -> None
        headers = _get_headers_from_task(sender)
        start_timestamp_ns = nanosecond_time()

        check_in_id = capture_checkin(
            monitor_slug=headers["sentry-celerybeat-name"],
            schedule=headers["sentry-celerybeat-schedule"],
            schedule_type=headers["sentry-celerybeat-schedule-type"],
            status=MonitorStatus.IN_PROGRESS,
        )

        headers.update({"sentry-check-in-id": check_in_id})
        headers.update({"sentry-start-timestamp-ns": start_timestamp_ns})

        sender.s().set(headers=headers)

    @task_success.connect  # type: ignore
    def celery_task_success(sender, **kwargs):
        # type: (celery.Task, Dict[Any, Any]) -> None
        headers = _get_headers_from_task(sender)
        start_timestamp_ns = headers["sentry-start-timestamp-ns"]

        capture_checkin(
            monitor_slug=headers["sentry-celerybeat-name"],
            check_in_id=headers["sentry-check-in-id"],
            schedule=headers["sentry-celerybeat-schedule"],
            schedule_type=headers["sentry-celerybeat-schedule-type"],
            duration_ns=nanosecond_time() - start_timestamp_ns,
            status=MonitorStatus.OK,
        )

    @task_failure.connect  # type: ignore
    def celery_task_failure(sender, **kwargs):
        # type: (celery.Task, Dict[Any, Any]) -> None
        headers = _get_headers_from_task(sender)
        start_timestamp_ns = headers["sentry-start-timestamp-ns"]

        capture_checkin(
            monitor_slug=headers["sentry-celerybeat-name"],
            check_in_id=headers["sentry-check-in-id"],
            schedule=headers["sentry-celerybeat-schedule"],
            schedule_type=headers["sentry-celerybeat-schedule-type"],
            duration_ns=nanosecond_time() - start_timestamp_ns,
            status=MonitorStatus.ERROR,
        )

    @task_retry.connect  # type: ignore
    def celery_task_retry(sender, **kwargs):
        # type: (celery.Task, Dict[Any, Any]) -> None
        headers = _get_headers_from_task(sender)
        start_timestamp_ns = headers["sentry-start-timestamp-ns"]

        capture_checkin(
            monitor_slug=headers["sentry-celerybeat-name"],
            check_in_id=headers["sentry-check-in-id"],
            schedule=headers["sentry-celerybeat-schedule"],
            schedule_type=headers["sentry-celerybeat-schedule-type"],
            duration_ns=nanosecond_time() - start_timestamp_ns,
            status=MonitorStatus.ERROR,
        )
