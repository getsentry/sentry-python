import functools
import re
import time
import weakref

import sentry_sdk
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.crons import MonitorStatus, capture_checkin
from sentry_sdk.crons.utils import _get_interval_schedule
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import Scope, should_send_default_pii
from sentry_sdk.traces import SegmentNameSource
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    capture_internal_exceptions,
    event_from_exception,
    format_timestamp,
    has_data_collection_enabled,
    logger,
    match_regex_list,
    now,
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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, List, Optional, Tuple

    from rq.job import Job

    from sentry_sdk._types import Event, EventProcessor, MonitorConfig
    from sentry_sdk.utils import ExcInfo

# rq generates `str(uuid4())` job ids.
_GENERATED_JOB_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# `*`, `*/n`, `n`, `n/n`, `n-n` and `n-n/n`.
_CRONTAB_ITEM = re.compile(r"\*(?:/\d+)?|(\d+)(?:-(\d+)(?:/\d+)?|/\d+)?")


class RqIntegration(Integration):
    identifier = "rq"
    origin = f"auto.queue.{identifier}"

    def __init__(
        self,
        monitor_cron_jobs: bool = False,
        exclude_cron_jobs: "Optional[List[str]]" = None,
    ) -> None:
        self.monitor_cron_jobs = monitor_cron_jobs
        self.exclude_cron_jobs = exclude_cron_jobs

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
            integration = client.get_integration(RqIntegration)
            if integration is None:
                return old_perform_job(self, job, queue, *args, **kwargs)

            check_in = None
            with capture_internal_exceptions():
                check_in = _start_check_in(integration, job)

            status = MonitorStatus.ERROR
            try:
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
                                span.set_attribute(
                                    SPANDATA.CODE_FUNCTION_NAME, func_name
                                )

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
                            span.set_data(
                                SPANDATA.MESSAGING_DESTINATION_NAME, queue.name
                            )

                            rv = old_perform_job(self, job, queue, *args, **kwargs)
                status = MonitorStatus.OK if rv else MonitorStatus.ERROR
            finally:
                if check_in is not None:
                    with capture_internal_exceptions():
                        monitor_slug, monitor_config, check_in_id, start = check_in
                        capture_checkin(
                            monitor_slug=monitor_slug,
                            monitor_config=monitor_config,
                            check_in_id=check_in_id,
                            duration=now() - start,
                            status=status,
                        )

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

            return old_enqueue_job(self, job, **kwargs)

        Queue.enqueue_job = sentry_patched_enqueue_job

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


def _is_portable_crontab_field(field: str, is_weekday: bool) -> bool:
    for item in field.split(","):
        match = _CRONTAB_ITEM.fullmatch(item)
        if match is None:
            return False
        first, last = match.groups()
        # rq-scheduler's crontab parser and Sentry read these differently.
        if last is not None and int(last) <= int(first):
            return False
        if is_weekday and first is not None and last is None and "/" in item:
            return False
    return True


def _get_crontab_config(cron_string: str) -> "Optional[MonitorConfig]":
    fields = cron_string.split()
    if len(fields) != 5 or not all(
        _is_portable_crontab_field(field, i == 4) for i, field in enumerate(fields)
    ):
        return None
    # Day of month and day of week are matched differently by different
    # cron parsers when both are set.
    if fields[2] != "*" and fields[4] != "*":
        return None
    # rq-scheduler evaluates crontabs in the system's local time.
    if time.timezone != 0 or (time.daylight and time.altzone != 0):
        return None
    return {
        "schedule": {"type": "crontab", "value": " ".join(fields)},
        "timezone": "UTC",
    }


def _get_monitor_config(job: "Job") -> "Optional[MonitorConfig]":
    # Jobs with `repeat` stop after a number of runs.
    if job.meta.get("repeat") is not None:
        return None

    cron_string = job.meta.get("cron_string")
    if isinstance(cron_string, str):
        return _get_crontab_config(cron_string)

    interval = job.meta.get("interval")
    if isinstance(interval, int):
        schedule = _get_interval_schedule(interval)
        if schedule is not None:
            return {"schedule": schedule}
    return None


def _start_check_in(
    integration: "RqIntegration", job: "Job"
) -> "Optional[Tuple[str, MonitorConfig, Optional[str], float]]":
    """
    Send an `in_progress` check-in for jobs that rq-scheduler's `cron()` and
    `schedule(interval=...)` re-enqueue. It stores their schedule in the meta.
    """
    if not integration.monitor_cron_jobs:
        return None
    if "cron_string" not in job.meta and "interval" not in job.meta:
        return None

    # Without an explicit `id`, every scheduled job gets a random one.
    if _GENERATED_JOB_ID.fullmatch(job.id):
        monitor_slug = job.func_name
    else:
        monitor_slug = job.id
    if match_regex_list(monitor_slug, integration.exclude_cron_jobs):
        return None

    monitor_config = _get_monitor_config(job)
    if monitor_config is None:
        logger.debug(
            "Not monitoring RQ job %r: its schedule can't be expressed as a "
            "Sentry Crons schedule.",
            monitor_slug,
        )
        return None

    check_in_id = capture_checkin(
        monitor_slug=monitor_slug,
        monitor_config=monitor_config,
        status=MonitorStatus.IN_PROGRESS,
    )
    return monitor_slug, monitor_config, check_in_id, now()
