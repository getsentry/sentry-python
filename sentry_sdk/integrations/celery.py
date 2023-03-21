from __future__ import absolute_import

import sys
import shutil
import functools

from sentry_sdk.consts import OP
from sentry_sdk._compat import reraise
from sentry_sdk._functools import wraps
from sentry_sdk.crons import capture_checkin, MonitorStatus
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.tracing import Transaction, TRANSACTION_SOURCE_TASK
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    nanosecond_time,
    logger,
)

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional
    from typing import Tuple
    from typing import TypeVar
    from typing import Union

    from sentry_sdk._types import EventProcessor, Event, Hint, ExcInfo

    F = TypeVar("F", bound=Callable[..., Any])


try:
    from celery import VERSION as CELERY_VERSION
    from celery import Task, Celery
    from celery.app.trace import task_has_custom
    from celery.beat import Service  # type: ignore
    from celery.exceptions import (  # type: ignore
        Ignore,
        Reject,
        Retry,
        SoftTimeLimitExceeded,
    )
    from celery.schedules import crontab, schedule  # type: ignore
    from celery.signals import (  # type: ignore
        beat_init,
        task_prerun,
        task_failure,
        task_success,
        task_retry,
    )
except ImportError:
    raise DidNotEnable("Celery not installed")


CELERY_CONTROL_FLOW_EXCEPTIONS = (Retry, Ignore, Reject)


class CeleryIntegration(Integration):
    identifier = "celery"

    def __init__(self, propagate_traces=True, celery_app=None):
        # type: (bool, Celery) -> None
        self.propagate_traces = propagate_traces

        if celery_app is not None:
            _patch_celery_beat_tasks(celery_app)

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


def _get_headers(task):
    # type: (Task) -> Dict[str, Any]
    headers = task.request.get("headers") or {}
    return headers


# Nested functions do not work as Celery hook receiver,
# so defining it here explicitly
celery_beat_init = None


def _patch_celery_beat_tasks(app):
    # type: (Celery) -> None

    global celery_beat_init

    def celery_beat_init(sender, **kwargs):
        # type: (Service, Dict[Any, Any]) -> None

        # Because we restart Celery Beat,
        # make sure that this will not be called infinitely
        beat_init.disconnect(celery_beat_init)

        add_updated_periodic_tasks = []

        for name in sender.scheduler.schedule.keys():
            # Ignore Celery's internal tasks
            if name.startswith("celery."):
                continue

            schedule_entry = sender.scheduler.schedule[name]
            celery_schedule = schedule_entry.schedule

            monitor_name = name
            monitor_schedule = None  # type: Optional[Union[str,Tuple[int, str]]]

            if isinstance(celery_schedule, crontab):
                monitor_schedule_type = "cron"
                monitor_schedule = (
                    "{0._orig_minute} "
                    "{0._orig_hour} "
                    "{0._orig_day_of_month} "
                    "{0._orig_month_of_year} "
                    "{0._orig_day_of_week}".format(celery_schedule)
                )
            elif isinstance(celery_schedule, schedule):
                monitor_schedule_type = "interval"
                monitor_schedule = (
                    int(celery_schedule.seconds),
                    "second",
                )
            else:
                logger.warning(
                    "Celery schedule type '%s' not supported by Sentry Crons.",
                    type(celery_schedule),
                )
                continue

            headers = schedule_entry.options.pop("headers", {})
            headers.update(
                {
                    "headers": {
                        "sentry-monitor-slug": monitor_name,
                        "sentry-monitor-schedule": monitor_schedule,
                        "sentry-monitor-schedule-type": monitor_schedule_type,
                    },
                }
            )

            task_signature = app.tasks.get(schedule_entry.task).s()
            task_signature.set(headers=headers)

            add_updated_periodic_tasks.append(
                functools.partial(
                    app.add_periodic_task,
                    celery_schedule,
                    task_signature,
                    args=schedule_entry.args,
                    kwargs=schedule_entry.kwargs,
                    name=schedule_entry.name,
                    **(schedule_entry.options or {}),
                )
            )

        # Stop Celery Beat
        sender.stop()

        # Update tasks to include Monitor information in headers
        for add_updated_periodic_task in add_updated_periodic_tasks:
            add_updated_periodic_task()

        # Start Celery Beat (with new (cloned) schedule, because old one is still in use)
        new_schedule_filename = sender.schedule_filename + ".new"
        shutil.copy2(sender.schedule_filename, new_schedule_filename)
        app.Beat(schedule=new_schedule_filename).run()

    beat_init.connect(celery_beat_init)
    task_prerun.connect(celery_task_before_run)
    task_success.connect(celery_task_success)
    task_failure.connect(celery_task_failure)
    task_retry.connect(celery_task_retry)


def celery_task_before_run(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_before_run %s", sender)
    headers = _get_headers(sender)
    start_timestamp_ns = nanosecond_time()

    check_in_id = capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        schedule=headers["sentry-monitor-schedule"],
        schedule_type=headers["sentry-monitor-schedule-type"],
        status=MonitorStatus.IN_PROGRESS,
    )

    headers.update({"sentry-monitor-check-in-id": check_in_id})
    headers.update({"sentry-monitor-start-timestamp-ns": start_timestamp_ns})

    sender.s().set(headers=headers)


def celery_task_success(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_success %s", sender)
    headers = _get_headers(sender)
    start_timestamp_ns = headers["sentry-monitor-start-timestamp-ns"]

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        check_in_id=headers["sentry-monitor-check-in-id"],
        schedule=headers["sentry-monitor-schedule"],
        schedule_type=headers["sentry-monitor-schedule-type"],
        duration_ns=nanosecond_time() - start_timestamp_ns,
        status=MonitorStatus.OK,
    )


def celery_task_failure(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_failure %s", sender)
    headers = _get_headers(sender)
    start_timestamp_ns = headers["sentry-monitor-start-timestamp-ns"]

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        check_in_id=headers["sentry-monitor-check-in-id"],
        schedule=headers["sentry-monitor-schedule"],
        schedule_type=headers["sentry-monitor-schedule-type"],
        duration_ns=nanosecond_time() - start_timestamp_ns,
        status=MonitorStatus.ERROR,
    )


def celery_task_retry(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_retry %s", sender)
    headers = _get_headers(sender)
    start_timestamp_ns = headers["sentry-monitor-start-timestamp-ns"]

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        check_in_id=headers["sentry-monitor-check-in-id"],
        schedule=headers["sentry-monitor-schedule"],
        schedule_type=headers["sentry-monitor-schedule-type"],
        duration_ns=nanosecond_time() - start_timestamp_ns,
        status=MonitorStatus.ERROR,
    )
