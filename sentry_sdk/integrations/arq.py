import sys
import time
from datetime import timedelta, timezone

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.crons import monitor
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SegmentNameSource
from sentry_sdk.tracing import Transaction, TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    _register_control_flow_exception,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    has_data_collection_enabled,
    logger,
    match_regex_list,
    parse_version,
    reraise,
)

try:
    import arq.worker
    from arq.connections import ArqRedis
    from arq.typing import WEEKDAYS
    from arq.version import VERSION as ARQ_VERSION
    from arq.worker import JobExecutionFailed, Retry, RetryJob, Worker
except ImportError:
    raise DidNotEnable("Arq is not installed")

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Union

    from arq.cron import CronJob
    from arq.jobs import Job
    from arq.typing import WorkerCoroutine
    from arq.worker import Function

    from sentry_sdk._types import Event, EventProcessor, ExcInfo, Hint, MonitorConfig

ARQ_CONTROL_FLOW_EXCEPTIONS = (JobExecutionFailed, Retry, RetryJob)


class ArqIntegration(Integration):
    identifier = "arq"
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
        try:
            if isinstance(ARQ_VERSION, str):
                version = parse_version(ARQ_VERSION)
            else:
                version = ARQ_VERSION.version[:2]

        except (TypeError, ValueError):
            version = None

        _check_minimum_version(ArqIntegration, version)

        patch_enqueue_job()
        patch_run_job()
        patch_create_worker()

        _register_control_flow_exception(ARQ_CONTROL_FLOW_EXCEPTIONS)  # type: ignore

        ignore_logger("arq.worker")


def patch_enqueue_job() -> None:
    old_enqueue_job = ArqRedis.enqueue_job
    original_kwdefaults = old_enqueue_job.__kwdefaults__

    async def _sentry_enqueue_job(
        self: "ArqRedis", function: str, *args: "Any", **kwargs: "Any"
    ) -> "Optional[Job]":
        client = sentry_sdk.get_client()
        if client.get_integration(ArqIntegration) is None:
            return await old_enqueue_job(self, function, *args, **kwargs)

        if has_span_streaming_enabled(client.options):
            if sentry_sdk.traces.get_current_span() is None:
                return await old_enqueue_job(self, function, *args, **kwargs)

            with sentry_sdk.traces.start_span(
                name=function,
                attributes={
                    "sentry.op": OP.QUEUE_SUBMIT_ARQ,
                    "sentry.origin": ArqIntegration.origin,
                },
            ):
                return await old_enqueue_job(self, function, *args, **kwargs)

        with sentry_sdk.start_span(
            op=OP.QUEUE_SUBMIT_ARQ, name=function, origin=ArqIntegration.origin
        ):
            return await old_enqueue_job(self, function, *args, **kwargs)

    _sentry_enqueue_job.__kwdefaults__ = original_kwdefaults
    ArqRedis.enqueue_job = _sentry_enqueue_job


def patch_run_job() -> None:
    old_run_job = Worker.run_job

    async def _sentry_run_job(self: "Worker", job_id: str, score: int) -> None:
        client = sentry_sdk.get_client()
        if client.get_integration(ArqIntegration) is None:
            return await old_run_job(self, job_id, score)

        with sentry_sdk.isolation_scope() as scope:
            scope._name = "arq"
            scope.clear_breadcrumbs()

            if has_span_streaming_enabled(client.options):
                with sentry_sdk.traces.start_span(
                    name="unknown arq task",
                    attributes={
                        "sentry.op": OP.QUEUE_TASK_ARQ,
                        "sentry.origin": ArqIntegration.origin,
                        "sentry.segment.name.source": SegmentNameSource.TASK,
                        SPANDATA.MESSAGING_MESSAGE_ID: job_id,
                    },
                    parent_span=None,
                ) as span:
                    if self.queue_name is not None:
                        span.set_attribute(
                            SPANDATA.MESSAGING_DESTINATION_NAME, self.queue_name
                        )
                    return await old_run_job(self, job_id, score)

            transaction = Transaction(
                name="unknown arq task",
                status="ok",
                op=OP.QUEUE_TASK_ARQ,
                source=TransactionSource.TASK,
                origin=ArqIntegration.origin,
            )

            with sentry_sdk.start_transaction(transaction) as span:
                if self.queue_name is not None:
                    span.set_data(SPANDATA.MESSAGING_DESTINATION_NAME, self.queue_name)
                return await old_run_job(self, job_id, score)

    Worker.run_job = _sentry_run_job


def _capture_exception(exc_info: "ExcInfo") -> None:
    scope = sentry_sdk.get_current_scope()

    if scope.transaction is not None:
        if exc_info[0] in ARQ_CONTROL_FLOW_EXCEPTIONS:
            scope.transaction.set_status(SPANSTATUS.ABORTED)
            return

        scope.transaction.set_status(SPANSTATUS.INTERNAL_ERROR)

    if exc_info[0] in ARQ_CONTROL_FLOW_EXCEPTIONS:
        return

    event, hint = event_from_exception(
        exc_info,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": ArqIntegration.identifier, "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _make_event_processor(
    ctx: "Dict[Any, Any]", *args: "Any", **kwargs: "Any"
) -> "EventProcessor":
    def event_processor(event: "Event", hint: "Hint") -> "Optional[Event]":
        with capture_internal_exceptions():
            scope = sentry_sdk.get_current_scope()
            if scope.transaction is not None:
                scope.transaction.name = ctx["job_name"]
                event["transaction"] = ctx["job_name"]

            tags = event.setdefault("tags", {})
            tags["arq_task_id"] = ctx["job_id"]
            tags["arq_task_retry"] = ctx["job_try"] > 1
            extra = event.setdefault("extra", {})

            arq_job_dict = {
                "task": ctx["job_name"],
                "retry": ctx["job_try"],
            }
            client_options = sentry_sdk.get_client().options

            if has_data_collection_enabled(client_options):
                if client_options["data_collection"]["queues"]:
                    arq_job_dict["args"] = args
                    arq_job_dict["kwargs"] = kwargs
            elif should_send_default_pii():
                arq_job_dict["args"] = args
                arq_job_dict["kwargs"] = kwargs
            else:
                arq_job_dict["args"] = SENSITIVE_DATA_SUBSTITUTE
                arq_job_dict["kwargs"] = SENSITIVE_DATA_SUBSTITUTE

            extra["arq-job"] = arq_job_dict

        return event

    return event_processor


def _wrap_coroutine(name: str, coroutine: "WorkerCoroutine") -> "WorkerCoroutine":
    async def _sentry_coroutine(
        ctx: "Dict[Any, Any]", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(ArqIntegration)
        if integration is None:
            return await coroutine(ctx, *args, **kwargs)

        if has_span_streaming_enabled(client.options):
            scope = sentry_sdk.get_current_scope()
            span = scope.streamed_span
            if span is not None:
                span.name = name

            scope.set_transaction_name(name)

        sentry_sdk.get_isolation_scope().add_event_processor(
            _make_event_processor({**ctx, "job_name": name}, *args, **kwargs)
        )

        try:
            result = await coroutine(ctx, *args, **kwargs)
        except Exception:
            exc_info = sys.exc_info()
            _capture_exception(exc_info)
            reraise(*exc_info)

        return result

    return _sentry_coroutine


def patch_create_worker() -> None:
    old_create_worker = arq.worker.create_worker

    @ensure_integration_enabled(ArqIntegration, old_create_worker)
    def _sentry_create_worker(*args: "Any", **kwargs: "Any") -> "Worker":
        settings_cls = args[0] if args else kwargs.get("settings_cls")

        if isinstance(settings_cls, dict):
            if "functions" in settings_cls:
                settings_cls["functions"] = [
                    _get_arq_function(func)
                    for func in settings_cls.get("functions", [])
                ]
            if "cron_jobs" in settings_cls:
                settings_cls["cron_jobs"] = [
                    _get_arq_cron_job(cron_job)
                    for cron_job in settings_cls.get("cron_jobs", [])
                ]

        if hasattr(settings_cls, "functions"):
            settings_cls.functions = [  # type: ignore[union-attr]
                _get_arq_function(func)
                for func in settings_cls.functions  # type: ignore[union-attr]
            ]
        if hasattr(settings_cls, "cron_jobs"):
            settings_cls.cron_jobs = [  # type: ignore[union-attr]
                _get_arq_cron_job(cron_job)
                for cron_job in (settings_cls.cron_jobs or [])  # type: ignore[union-attr]
            ]

        if "functions" in kwargs:
            kwargs["functions"] = [
                _get_arq_function(func) for func in kwargs.get("functions", [])
            ]
        if "cron_jobs" in kwargs:
            kwargs["cron_jobs"] = [
                _get_arq_cron_job(cron_job) for cron_job in kwargs.get("cron_jobs", [])
            ]

        worker = old_create_worker(*args, **kwargs)

        integration = sentry_sdk.get_client().get_integration(ArqIntegration)
        if integration is not None and integration.monitor_cron_jobs:
            for cron_job in worker.cron_jobs:
                with capture_internal_exceptions():
                    _monitor_cron_job(integration, cron_job, worker)

        return worker

    arq.worker.create_worker = _sentry_create_worker


def _get_arq_function(func: "Union[str, Function, WorkerCoroutine]") -> "Function":
    arq_func = arq.worker.func(func)
    arq_func.coroutine = _wrap_coroutine(arq_func.name, arq_func.coroutine)

    return arq_func


def _get_arq_cron_job(cron_job: "CronJob") -> "CronJob":
    cron_job.coroutine = _wrap_coroutine(cron_job.name, cron_job.coroutine)

    return cron_job


def _monitor_cron_job(
    integration: "ArqIntegration", cron_job: "CronJob", worker: "Worker"
) -> None:
    # Each create_worker call wraps the coroutine again.
    if getattr(cron_job, "_sentry_monitored", False):
        return
    cron_job._sentry_monitored = True

    # arq names cron jobs "cron:<function name>" by default.
    monitor_slug = cron_job.name
    if monitor_slug.startswith("cron:"):
        monitor_slug = monitor_slug[len("cron:") :]
    if match_regex_list(monitor_slug, integration.exclude_cron_jobs):
        return

    monitor_config = _get_monitor_config(cron_job, worker)
    if monitor_config is None:
        logger.debug(
            "Not monitoring arq cron job %r: its schedule can't be expressed "
            "as a Sentry Crons schedule.",
            cron_job.name,
        )
        return

    coroutine = cron_job.coroutine

    async def _sentry_cron_coroutine(
        ctx: "Dict[Any, Any]", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        with monitor(monitor_slug=monitor_slug, monitor_config=monitor_config):
            return await coroutine(ctx, *args, **kwargs)

    cron_job.coroutine = _sentry_cron_coroutine


def _get_crontab_field(value: "Any", offset: int = 0) -> "Optional[str]":
    if value is None:
        return "*"
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (set, list, tuple)) or not value:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return None
    if offset:
        value = {(v + offset) % 7 for v in value}
    return ",".join(str(v) for v in sorted(value))


def _get_crontab(cron_job: "CronJob") -> "Optional[str]":
    # Sentry crontabs have minute resolution.
    if cron_job.second not in (0, {0}, [0], (0,)):
        return None

    weekday = cron_job.weekday
    if isinstance(weekday, str):
        weekday = WEEKDAYS.index(weekday.lower())

    # arq requires both to match, cron requires either.
    if cron_job.day is not None and weekday is not None:
        return None

    fields = [
        _get_crontab_field(cron_job.minute),
        _get_crontab_field(cron_job.hour),
        _get_crontab_field(cron_job.day),
        _get_crontab_field(cron_job.month),
        # arq counts weekdays from Monday = 0, cron from Sunday = 0.
        _get_crontab_field(weekday, offset=1),
    ]
    if None in fields:
        return None
    return " ".join(fields)  # type: ignore[arg-type]


def _get_timezone_name(worker: "Worker") -> "Optional[str]":
    tz = getattr(worker, "timezone", None)
    if tz is None:
        # Before arq 0.25, cron jobs ran in naive local time.
        if time.timezone == 0 and (not time.daylight or time.altzone == 0):
            return "UTC"
        return None

    # zoneinfo and pytz
    name = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if isinstance(name, str):
        return name

    # arq defaults to the system's fixed UTC offset at startup.
    if isinstance(tz, timezone):
        offset = tz.utcoffset(None)
        if offset % timedelta(hours=1):
            return None
        hours = offset // timedelta(hours=1)
        return "Etc/GMT%+d" % -hours if hours else "UTC"

    return None


def _get_monitor_config(
    cron_job: "CronJob", worker: "Worker"
) -> "Optional[MonitorConfig]":
    crontab = _get_crontab(cron_job)
    timezone_name = _get_timezone_name(worker)
    if crontab is None or timezone_name is None:
        return None
    return {
        "schedule": {"type": "crontab", "value": crontab},
        "timezone": timezone_name,
    }
