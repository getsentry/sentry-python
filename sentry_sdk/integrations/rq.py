import functools
import time
import weakref

import sentry_sdk
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.crons import MonitorStatus, capture_checkin
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import Scope, should_send_default_pii
from sentry_sdk.traces import SegmentNameSource
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    ContextVar,
    capture_internal_exceptions,
    event_from_exception,
    format_timestamp,
    has_data_collection_enabled,
    parse_version,
)

try:
    from rq.job import JobStatus
    from rq.queue import Queue
    from rq.timeouts import JobTimeoutException
    from rq.version import VERSION as RQ_VERSION
    from rq.worker import Worker
except ImportError:
    raise DidNotEnable("RQ not installed")

try:
    from rq.worker import BaseWorker

    if not hasattr(BaseWorker, "perform_job"):
        BaseWorker = None
except ImportError:
    BaseWorker = None

try:
    # RQ's built-in cron scheduler (`rq cron`), added in 2.4.
    from rq.cron import CronJob
except ImportError:
    CronJob = None

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Optional

    from rq.job import Job

    from sentry_sdk._types import (
        Event,
        EventProcessor,
        MonitorConfig,
        MonitorConfigScheduleUnit,
    )
    from sentry_sdk.utils import ExcInfo


class RqIntegration(Integration):
    identifier = "rq"
    origin = f"auto.queue.{identifier}"

    def __init__(self, monitor_cron_jobs: bool = False) -> None:
        self.monitor_cron_jobs = monitor_cron_jobs

    @staticmethod
    def setup_once() -> None:
        version = parse_version(RQ_VERSION)
        _check_minimum_version(RqIntegration, version)

        # In rq 2.7.0+, SimpleWorker inherits from BaseWorker directly
        # instead of Worker, so we need to patch BaseWorker to cover both.
        # For older versions where BaseWorker doesn't exist or doesn't have
        # perform_job, we patch Worker.
        worker_cls = BaseWorker if BaseWorker is not None else Worker

        old_perform_job = worker_cls.perform_job

        @functools.wraps(old_perform_job)
        def sentry_patched_perform_job(
            self: "Any", job: "Job", queue: "Queue", *args: "Any", **kwargs: "Any"
        ) -> bool:
            client = sentry_sdk.get_client()
            if client.get_integration(RqIntegration) is None:
                return old_perform_job(self, job, queue, *args, **kwargs)

            with sentry_sdk.new_scope() as scope:
                scope.clear_breadcrumbs()
                scope.add_event_processor(_make_event_processor(weakref.ref(job)))

                if has_span_streaming_enabled(client.options):
                    sentry_sdk.traces.continue_trace(
                        job.meta.get("_sentry_trace_headers") or {}
                    )

                    Scope.set_custom_sampling_context({"rq_job": job})

                    func_name = None
                    with capture_internal_exceptions():
                        func_name = job.func_name

                    with sentry_sdk.traces.start_span(
                        name="unknown RQ task" if func_name is None else func_name,
                        attributes={
                            "sentry.op": OP.QUEUE_TASK_RQ,
                            "sentry.origin": RqIntegration.origin,
                            "sentry.segment.name.source": SegmentNameSource.TASK,
                            SPANDATA.MESSAGING_MESSAGE_ID: job.id,
                            SPANDATA.MESSAGING_DESTINATION_NAME: queue.name,
                        },
                        parent_span=None,
                    ) as span:
                        if func_name is not None:
                            span.set_attribute(SPANDATA.CODE_FUNCTION_NAME, func_name)

                        rv = old_perform_job(self, job, queue, *args, **kwargs)
                else:
                    transaction = continue_trace(
                        job.meta.get("_sentry_trace_headers") or {},
                        op=OP.QUEUE_TASK_RQ,
                        name="unknown RQ task",
                        source=TransactionSource.TASK,
                        origin=RqIntegration.origin,
                    )

                    with capture_internal_exceptions():
                        transaction.name = job.func_name

                    with sentry_sdk.start_transaction(
                        transaction,
                        custom_sampling_context={"rq_job": job},
                    ) as span:
                        span.set_data(SPANDATA.MESSAGING_DESTINATION_NAME, queue.name)

                        rv = old_perform_job(self, job, queue, *args, **kwargs)

            with capture_internal_exceptions():
                _finish_cron_check_in(job)

            if self.is_horse:
                # We're inside of a forked process and RQ is
                # about to call `os._exit`. Make sure that our
                # events get sent out.
                sentry_sdk.get_client().flush()

            return rv

        worker_cls.perform_job = sentry_patched_perform_job

        old_handle_exception = worker_cls.handle_exception

        def sentry_patched_handle_exception(
            self: "Worker", job: "Any", *exc_info: "Any", **kwargs: "Any"
        ) -> "Any":
            retry = (
                hasattr(job, "retries_left")
                and job.retries_left
                and job.retries_left > 0
            )
            failed = job._status == JobStatus.FAILED or job.is_failed
            if failed and not retry:
                _capture_exception(exc_info)

            return old_handle_exception(self, job, *exc_info, **kwargs)

        worker_cls.handle_exception = sentry_patched_handle_exception

        old_enqueue_job = Queue.enqueue_job

        @functools.wraps(old_enqueue_job)
        def sentry_patched_enqueue_job(
            self: "Queue", job: "Any", **kwargs: "Any"
        ) -> "Any":
            client = sentry_sdk.get_client()
            if client.get_integration(RqIntegration) is None:
                return old_enqueue_job(self, job, **kwargs)

            scope = sentry_sdk.get_current_scope()
            span = (
                scope.streamed_span
                if has_span_streaming_enabled(client.options)
                else scope.span
            )
            if span is not None:
                job.meta["_sentry_trace_headers"] = dict(
                    scope.iter_trace_propagation_headers()
                )

            cron_check_in = _cron_check_in.get(None)
            if cron_check_in is not None:
                job.meta[_CRON_CHECK_IN_META_KEY] = cron_check_in

            return old_enqueue_job(self, job, **kwargs)

        Queue.enqueue_job = sentry_patched_enqueue_job

        _patch_cron_job_enqueue()

        ignore_logger("rq.worker")


def _make_event_processor(weak_job: "Callable[[], Job]") -> "EventProcessor":
    def event_processor(event: "Event", hint: "dict[str, Any]") -> "Event":
        job = weak_job()
        if job is not None:
            with capture_internal_exceptions():
                extra = event.setdefault("extra", {})
                rq_job = {
                    "job_id": job.id,
                    "func": job.func_name,
                    "description": job.description,
                }

                client_options = sentry_sdk.get_client().options
                if has_data_collection_enabled(client_options):
                    if client_options["data_collection"]["queues"]:
                        rq_job["args"] = job.args
                        rq_job["kwargs"] = job.kwargs
                elif should_send_default_pii():
                    rq_job["args"] = job.args
                    rq_job["kwargs"] = job.kwargs
                else:
                    rq_job["args"] = SENSITIVE_DATA_SUBSTITUTE
                    rq_job["kwargs"] = SENSITIVE_DATA_SUBSTITUTE

                if job.enqueued_at:
                    rq_job["enqueued_at"] = format_timestamp(job.enqueued_at)
                if job.started_at:
                    rq_job["started_at"] = format_timestamp(job.started_at)

                extra["rq-job"] = rq_job

        if "exc_info" in hint:
            with capture_internal_exceptions():
                if issubclass(hint["exc_info"][0], JobTimeoutException):
                    event["fingerprint"] = ["rq", "JobTimeoutException", job.func_name]

        return event

    return event_processor


def _capture_exception(exc_info: "ExcInfo", **kwargs: "Any") -> None:
    client = sentry_sdk.get_client()

    event, hint = event_from_exception(
        exc_info,
        client_options=client.options,
        mechanism={"type": "rq", "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)


# The check-in a `CronJob` opened in the cron scheduler process, handed to
# `Queue.enqueue_job` so that it's stored in the job's meta for the worker.
_CRON_CHECK_IN_META_KEY = "_sentry_cron_check_in"
_cron_check_in = ContextVar("sentry_rq_cron_check_in")

# Shorthands that Sentry accepts in place of a 5-field crontab.
_CRONTAB_SHORTHANDS = (
    "@yearly",
    "@annually",
    "@monthly",
    "@weekly",
    "@daily",
    "@hourly",
)

_INTERVAL_UNITS: "tuple[tuple[MonitorConfigScheduleUnit, int], ...]" = (
    ("day", 60 * 60 * 24),
    ("hour", 60 * 60),
    ("minute", 60),
)


def _get_cron_monitor_config(cron_job: "Any") -> "Optional[MonitorConfig]":
    cron = getattr(cron_job, "cron", None)  # Cron strings were added in RQ 2.5.
    if cron is not None:
        # Sentry doesn't take croniter's 6-field (seconds) crontabs.
        if len(cron.split()) != 5 and cron not in _CRONTAB_SHORTHANDS:
            return None
        # RQ evaluates cron strings in UTC.
        return {"schedule": {"type": "crontab", "value": cron}, "timezone": "UTC"}

    interval = cron_job.interval
    if interval is None:
        return None
    for unit, unit_seconds in _INTERVAL_UNITS:
        if interval >= unit_seconds and interval % unit_seconds == 0:
            return {
                "schedule": {
                    "type": "interval",
                    "value": int(interval // unit_seconds),
                    "unit": unit,
                },
            }
    return None


def _get_cron_monitor_slug(cron_job: "Any") -> str:
    # `CronJob.name` was added in RQ 2.11 and defaults to the same value.
    name = getattr(cron_job, "name", None)
    if name:
        return name
    return f"{cron_job.func.__module__}.{cron_job.func.__name__}"


def _patch_cron_job_enqueue() -> None:
    """
    Send an `in_progress` check-in, with a monitor config built from the cron
    job's schedule, when `rq cron` enqueues a job. The worker sends the
    closing check-in in `_finish_cron_check_in`.
    """
    if CronJob is None:
        return

    old_enqueue = CronJob.enqueue

    @functools.wraps(old_enqueue)
    def sentry_patched_cron_job_enqueue(
        self: "Any", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        integration = sentry_sdk.get_client().get_integration(RqIntegration)
        if integration is None or not integration.monitor_cron_jobs:
            return old_enqueue(self, *args, **kwargs)

        check_in = None
        with capture_internal_exceptions():
            monitor_config = _get_cron_monitor_config(self)
            if monitor_config is not None:
                monitor_slug = _get_cron_monitor_slug(self)
                check_in = {
                    "monitor_slug": monitor_slug,
                    "monitor_config": monitor_config,
                    "check_in_id": capture_checkin(
                        monitor_slug=monitor_slug,
                        monitor_config=monitor_config,
                        status=MonitorStatus.IN_PROGRESS,
                    ),
                    # Wall clock time, since the job finishes in another process.
                    "start_timestamp_s": time.time(),
                }

        if check_in is None:
            return old_enqueue(self, *args, **kwargs)

        token = _cron_check_in.set(check_in)
        try:
            return old_enqueue(self, *args, **kwargs)
        except BaseException:
            # Not enqueued, so no worker will close the check-in.
            with capture_internal_exceptions():
                _capture_cron_check_in(check_in, MonitorStatus.ERROR)
            raise
        finally:
            _cron_check_in.reset(token)

    CronJob.enqueue = sentry_patched_cron_job_enqueue


def _finish_cron_check_in(job: "Job") -> None:
    check_in = job.meta.get(_CRON_CHECK_IN_META_KEY)
    if check_in is None:
        return

    status = job.get_status(refresh=False)
    if status == JobStatus.FINISHED:
        _capture_cron_check_in(check_in, MonitorStatus.OK)
    elif status == JobStatus.FAILED:
        _capture_cron_check_in(check_in, MonitorStatus.ERROR)
    # Otherwise a retry is scheduled, which closes the check-in when it's done.


def _capture_cron_check_in(check_in: "Dict[str, Any]", status: str) -> None:
    capture_checkin(
        monitor_slug=check_in["monitor_slug"],
        monitor_config=check_in["monitor_config"],
        check_in_id=check_in["check_in_id"],
        duration=time.time() - check_in["start_timestamp_s"],
        status=status,
    )
