import re
import threading
import weakref
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.crons import MonitorStatus, capture_checkin
from sentry_sdk.crons.utils import _get_interval_schedule, _get_timezone_name
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import (
    capture_internal_exceptions,
    logger,
    match_regex_list,
    now,
    package_version,
)

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Tuple

    from apscheduler.events import JobExecutionEvent
    from apscheduler.job import Job
    from apscheduler.schedulers.base import BaseScheduler
    from apscheduler.triggers.base import BaseTrigger
    from apscheduler.triggers.cron.fields import BaseField

    from sentry_sdk._types import MonitorConfig

try:
    from apscheduler.events import (
        EVENT_JOB_ERROR,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_MISSED,
    )
    from apscheduler.executors.base import BaseExecutor, MaxInstancesReachedError
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.cron.expressions import (
        AllExpression,
        RangeExpression,
    )
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:
    raise DidNotEnable("APScheduler is not installed, or is not version 3.x")


class APSchedulerIntegration(Integration):
    identifier = "apscheduler"

    def __init__(self, exclude_cron_jobs: "Optional[List[str]]" = None) -> None:
        self.exclude_cron_jobs = exclude_cron_jobs

    @staticmethod
    def setup_once() -> None:
        version = package_version("apscheduler")
        _check_minimum_version(APSchedulerIntegration, version)

        _patch_submit_job()


class _PendingCheckIn:
    def __init__(
        self, monitor_slug: str, monitor_config: "MonitorConfig", start: float
    ) -> None:
        self.monitor_slug = monitor_slug
        self.monitor_config = monitor_config
        self.start = start
        self.opened = False
        self.check_in_id: "Optional[str]" = None

    def open(self) -> None:
        self.opened = True
        self.check_in_id = capture_checkin(
            monitor_slug=self.monitor_slug,
            monitor_config=self.monitor_config,
            status=MonitorStatus.IN_PROGRESS,
        )

    def close(self, status: str) -> None:
        # Synchronous executors finish the run before `submit_job` returns.
        if not self.opened:
            self.open()

        capture_checkin(
            monitor_slug=self.monitor_slug,
            monitor_config=self.monitor_config,
            check_in_id=self.check_in_id,
            duration=now() - self.start,
            status=status,
        )


class _CronsListener:
    """Closes the check-ins of submitted runs when their execution event arrives."""

    MASK = EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: "Dict[Tuple[str, datetime], _PendingCheckIn]" = {}

    def add(
        self, job_id: str, run_time: "datetime", pending: "_PendingCheckIn"
    ) -> None:
        with self.lock:
            self.pending[(job_id, run_time)] = pending

    def open(self, job_id: str, run_time: "datetime") -> None:
        # Under the lock, so `in_progress` is always sent before `finish`
        # sends the closing check-in.
        with self.lock:
            pending = self.pending.get((job_id, run_time))
            if pending is not None and not pending.opened:
                pending.open()

    def discard(self, job_id: str, run_time: "datetime") -> None:
        with self.lock:
            self.pending.pop((job_id, run_time), None)

    def finish(self, job_id: str, run_time: "datetime", status: str) -> None:
        with self.lock:
            pending = self.pending.pop((job_id, run_time), None)
        if pending is not None:
            pending.close(status)

    def finish_missed(self, job_id: str, run_time: "datetime") -> None:
        with self.lock:
            pending = self.pending.pop((job_id, run_time), None)
        # Send nothing, so Sentry reports the slot as missed, unless a run that
        # waited in a busy pool already sent `in_progress`.
        if pending is not None and pending.opened:
            pending.close(MonitorStatus.ERROR)

    def __call__(self, event: "JobExecutionEvent") -> None:
        with capture_internal_exceptions():
            if event.code == EVENT_JOB_MISSED:
                self.finish_missed(event.job_id, event.scheduled_run_time)
                return

            status = (
                MonitorStatus.OK
                if event.code == EVENT_JOB_EXECUTED
                else MonitorStatus.ERROR
            )
            self.finish(event.job_id, event.scheduled_run_time, status)


# Weak keys so we don't keep schedulers alive.
_listeners: "weakref.WeakKeyDictionary[BaseScheduler, _CronsListener]" = (
    weakref.WeakKeyDictionary()
)
_listeners_lock = threading.Lock()


def _get_listener(scheduler: "BaseScheduler") -> "_CronsListener":
    with _listeners_lock:
        listener = _listeners.get(scheduler)
        if listener is None:
            listener = _CronsListener()
            scheduler.add_listener(listener, _CronsListener.MASK)
            _listeners[scheduler] = listener
        return listener


# APScheduler assigns `uuid4().hex` when a job is added without an `id`.
_GENERATED_JOB_ID = re.compile(r"[0-9a-f]{32}")


def _get_crontab_day_of_week(field: "BaseField") -> "Optional[str]":
    # APScheduler counts weekdays from Monday = 0, cron from Sunday = 0. Emit
    # Monday = 1 ... Sunday = 7, which cron also accepts, so ranges stay ranges.
    values = []
    for expr in field.expressions:
        if type(expr) is AllExpression:
            if expr.step is None:
                return "*"
            first, last, step = 0, 6, expr.step
        elif isinstance(expr, RangeExpression):
            first = expr.first
            last = 6 if expr.last is None else expr.last
            step = expr.step
        else:
            return None

        value = str(first + 1)
        if last != first:
            value += f"-{last + 1}"
        if step:
            value += f"/{step}"
        values.append(value)

    return ",".join(values)


def _get_crontab_field(field: "BaseField") -> "Optional[str]":
    for expr in field.expressions:
        # Rules out "last", "1st mon" and the like.
        if not (type(expr) is AllExpression or isinstance(expr, RangeExpression)):
            return None
    return str(field)


def _get_crontab(trigger: "CronTrigger") -> "Optional[str]":
    fields = {field.name: field for field in trigger.fields}

    # Sentry only takes 5-field crontabs.
    if str(fields["second"]) != "0":
        return None
    if str(fields["year"]) != "*" or str(fields["week"]) != "*":
        return None

    day = _get_crontab_field(fields["day"])
    day_of_week = _get_crontab_day_of_week(fields["day_of_week"])
    if day is None or day_of_week is None:
        return None

    # APScheduler requires both to match, cron requires either.
    if day != "*" and day_of_week != "*":
        return None

    minute = _get_crontab_field(fields["minute"])
    hour = _get_crontab_field(fields["hour"])
    month = _get_crontab_field(fields["month"])
    if minute is None or hour is None or month is None:
        return None

    return f"{minute} {hour} {day} {month} {day_of_week}"


def _get_monitor_config(trigger: "BaseTrigger") -> "Optional[MonitorConfig]":
    monitor_config: "MonitorConfig"
    if isinstance(trigger, CronTrigger):
        crontab = _get_crontab(trigger)
        if crontab is None:
            return None
        # Without a timezone Sentry would read the crontab as UTC.
        timezone_name = _get_timezone_name(trigger.timezone)
        if timezone_name is None:
            return None
        monitor_config = {
            "schedule": {"type": "crontab", "value": crontab},
            "timezone": timezone_name,
        }
    elif isinstance(trigger, IntervalTrigger):
        schedule = _get_interval_schedule(trigger.interval.total_seconds())
        if schedule is None:
            return None
        monitor_config = {"schedule": schedule}
    else:
        return None

    # Sentry schedules have no end, so finite triggers would alert as missed.
    if trigger.end_date is not None:
        return None

    return monitor_config


def _is_missed(job: "Job", run_time: "datetime") -> bool:
    # The same test run_job uses to skip a run and report it missed.
    grace_time = job.misfire_grace_time
    if grace_time is None:
        return False
    return datetime.now(timezone.utc) - run_time > timedelta(seconds=grace_time)


def _begin_check_in(
    integration: "APSchedulerIntegration",
    executor: "BaseExecutor",
    job: "Job",
    run_times: "List[datetime]",
) -> "Optional[_CronsListener]":
    if getattr(job.func, "_sentry_monitored", False):
        return None

    # Pool executors send `in_progress` before the run starts, so skip missed
    # runs here.
    if _is_missed(job, run_times[-1]):
        return None

    # Generated ids change on every restart, which would create a new monitor
    # each time. Fall back to the job name (the function name by default).
    has_generated_id = _GENERATED_JOB_ID.fullmatch(job.id) is not None
    monitor_slug = job.name if has_generated_id else job.id
    if match_regex_list(monitor_slug, integration.exclude_cron_jobs):
        return None

    monitor_config = _get_monitor_config(job.trigger)
    if monitor_config is None:
        logger.debug(
            "Not monitoring APScheduler job %r: its trigger %r can't be "
            "expressed as a Sentry Crons schedule, or its timezone has no "
            "IANA name.",
            job.id,
            job.trigger,
        )
        return None

    if has_generated_id:
        logger.debug(
            "APScheduler job %r has no explicit `id`, so its name is used as its "
            "monitor slug, and jobs with the same name share a monitor. Pass `id` "
            "to `add_job` to give it its own monitor.",
            job.name,
        )

    listener = _get_listener(executor._scheduler)
    listener.add(
        job.id, run_times[-1], _PendingCheckIn(monitor_slug, monitor_config, now())
    )
    return listener


def _patch_submit_job() -> None:
    """Send an `in_progress` check-in once the executor accepts a due job."""
    old_submit_job = BaseExecutor.submit_job

    @wraps(old_submit_job)
    def sentry_submit_job(
        self: "BaseExecutor",
        job: "Job",
        run_times: "List[datetime]",
        *args: "Any",
        **kwargs: "Any",
    ) -> None:
        integration = sentry_sdk.get_client().get_integration(APSchedulerIntegration)
        if integration is None:
            return old_submit_job(self, job, run_times, *args, **kwargs)

        # A backlog of run times runs in one submit. Check in once, and close it
        # with the last run time's event.
        listener = None
        with capture_internal_exceptions():
            listener = _begin_check_in(integration, self, job, run_times)

        try:
            old_submit_job(self, job, run_times, *args, **kwargs)
        except MaxInstancesReachedError:
            if listener is not None:
                with capture_internal_exceptions():
                    listener.discard(job.id, run_times[-1])
            raise
        except BaseException:
            # Not submitted, so no execution event will close the check-in.
            if listener is not None:
                with capture_internal_exceptions():
                    listener.finish(job.id, run_times[-1], MonitorStatus.ERROR)
            raise

        if listener is not None:
            with capture_internal_exceptions():
                listener.open(job.id, run_times[-1])

    BaseExecutor.submit_job = sentry_submit_job
