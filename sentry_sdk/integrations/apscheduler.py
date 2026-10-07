import re
import threading
import weakref
from functools import lru_cache, wraps
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.crons import MonitorStatus, capture_checkin
from sentry_sdk.crons.utils import _get_interval_schedule
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.utils import (
    capture_internal_exceptions,
    logger,
    match_regex_list,
    now,
    package_version,
)

if TYPE_CHECKING:
    from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

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

    def __init__(
        self,
        monitor_jobs: bool = False,
        exclude_jobs: "Optional[List[str]]" = None,
    ) -> None:
        self.monitor_jobs = monitor_jobs
        self.exclude_jobs = exclude_jobs

    @staticmethod
    def setup_once() -> None:
        version = package_version("apscheduler")
        _check_minimum_version(APSchedulerIntegration, version)
        if version is not None and version[0] >= 4:
            raise DidNotEnable("APScheduler 4 is not supported.")

        _patch_submit_job()


class _PendingCheckIn:
    __slots__ = ("monitor_slug", "monitor_config", "start", "opened", "check_in_id")

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


class _CronsListener:
    """
    Tracks the check-ins of submitted runs and closes them.

    Executors dispatch one `EVENT_JOB_EXECUTED`, `EVENT_JOB_ERROR` or
    `EVENT_JOB_MISSED` per submitted run time, in the scheduler's process, for
    every executor type.
    """

    MASK = EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # Keyed by (job id, scheduled run time).
        self.pending: "Dict[Tuple[str, Any], _PendingCheckIn]" = {}

    def add(self, job_id: str, run_time: "Any", pending: "_PendingCheckIn") -> None:
        with self.lock:
            self.pending[(job_id, run_time)] = pending

    def open(self, job_id: str, run_time: "Any") -> None:
        # Under the lock, so `in_progress` is always sent before `finish`
        # sends the closing check-in.
        with self.lock:
            pending = self.pending.get((job_id, run_time))
            if pending is not None and not pending.opened:
                pending.open()

    def discard(self, job_id: str, run_time: "Any") -> None:
        with self.lock:
            self.pending.pop((job_id, run_time), None)

    def finish(self, job_id: str, run_time: "Any", status: str) -> None:
        with self.lock:
            pending = self.pending.pop((job_id, run_time), None)
        if pending is None:
            return

        # Synchronous executors finish the run before `submit_job` returns.
        if not pending.opened:
            pending.open()

        capture_checkin(
            monitor_slug=pending.monitor_slug,
            monitor_config=pending.monitor_config,
            check_in_id=pending.check_in_id,
            duration=now() - pending.start,
            status=status,
        )

    def __call__(self, event: "Any") -> None:
        with capture_internal_exceptions():
            status = (
                MonitorStatus.OK
                if event.code == EVENT_JOB_EXECUTED
                else MonitorStatus.ERROR
            )
            self.finish(event.job_id, event.scheduled_run_time, status)


# One listener per scheduler. Weak keys so we don't keep schedulers alive.
_listeners: "weakref.WeakKeyDictionary[Any, _CronsListener]" = (
    weakref.WeakKeyDictionary()
)
_listeners_lock = threading.Lock()


def _get_listener(scheduler: "Any") -> "_CronsListener":
    with _listeners_lock:
        listener = _listeners.get(scheduler)
        if listener is None:
            listener = _CronsListener()
            scheduler.add_listener(listener, _CronsListener.MASK)
            _listeners[scheduler] = listener
        return listener


# APScheduler assigns `uuid4().hex` when a job is added without an `id`.
_GENERATED_JOB_ID = re.compile(r"[0-9a-f]{32}")

# Generated job ids already warned about.
_warned_job_ids: "Set[str]" = set()


def _has_generated_id(job: "Any") -> bool:
    return _GENERATED_JOB_ID.fullmatch(job.id) is not None


def _get_monitor_slug(job: "Any") -> str:
    # Generated ids change on every restart, which would create a new monitor
    # each time. Fall back to the job name (the function name by default).
    if _has_generated_id(job):
        return job.name
    return job.id


def _warn_generated_id(job: "Any") -> None:
    if job.id in _warned_job_ids:
        return
    _warned_job_ids.add(job.id)
    logger.warning(
        "APScheduler job %r has no explicit `id`, so its name is used as its "
        "monitor slug, and jobs with the same name share a monitor. Pass `id` "
        "to `add_job` to give it its own monitor.",
        job.name,
    )


@lru_cache(maxsize=None)
def _get_known_timezones() -> "FrozenSet[str]":
    try:
        from zoneinfo import available_timezones
    except ImportError:
        try:
            from backports.zoneinfo import available_timezones  # type: ignore
        except ImportError:
            try:
                import pytz  # type: ignore
            except ImportError:
                return frozenset(("UTC",))
            # A lazy list that frozenset() alone would read as empty.
            return frozenset(name for name in pytz.all_timezones)
    return frozenset(available_timezones())


def _get_timezone_name(timezone: "Any") -> "Optional[str]":
    """
    Return the IANA name of the timezone, or None if it has none, e.g.
    `datetime.timezone(timedelta(hours=2))` ("UTC+02:00"), `pytz.FixedOffset`
    or tzlocal's "local".
    """
    name = str(timezone)
    if name in _get_known_timezones():
        return name
    return None


def _get_crontab_day_of_week(field: "Any") -> "Optional[str]":
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


def _get_crontab_field(field: "Any") -> "Optional[str]":
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


def _get_monitor_config(trigger: "Any") -> "Optional[MonitorConfig]":
    if isinstance(trigger, CronTrigger):
        crontab = _get_crontab(trigger)
        if crontab is None:
            return None
        # Without a timezone Sentry would read the crontab as UTC.
        timezone = _get_timezone_name(trigger.timezone)
        if timezone is None:
            return None
        return {
            "schedule": {"type": "crontab", "value": crontab},
            "timezone": timezone,
        }

    if isinstance(trigger, IntervalTrigger):
        schedule = _get_interval_schedule(trigger.interval.total_seconds())
        if schedule is None:
            return None
        return {"schedule": schedule}

    # DateTrigger (one-off) and combining triggers have no schedule to monitor.
    return None


def _begin_check_ins(
    integration: "APSchedulerIntegration",
    executor: "Any",
    job: "Any",
    run_times: "List[Any]",
) -> "Optional[_CronsListener]":
    monitor_slug = _get_monitor_slug(job)
    if match_regex_list(monitor_slug, integration.exclude_jobs):
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

    if _has_generated_id(job):
        _warn_generated_id(job)

    listener = _get_listener(executor._scheduler)
    start = now()

    # Each run time gets its own execution event, so its own check-in.
    for run_time in run_times:
        listener.add(
            job.id, run_time, _PendingCheckIn(monitor_slug, monitor_config, start)
        )

    return listener


def _patch_submit_job() -> None:
    """
    Send an `in_progress` check-in, with the monitor config derived from the
    job's trigger, when the scheduler hands a due job to its executor.
    `_CronsListener` sends the closing `ok`/`error` check-in.

    `submit_job` is called in the scheduler's process for every executor type
    and is the last place where the `Job` (and so its trigger) is at hand.
    The check-ins are opened only once it accepts the job, so runs skipped
    because of `max_instances` get none.
    """
    old_submit_job = BaseExecutor.submit_job

    @wraps(old_submit_job)
    def sentry_submit_job(
        self: "Any", job: "Any", run_times: "List[Any]", *args: "Any", **kwargs: "Any"
    ) -> "Any":
        integration = sentry_sdk.get_client().get_integration(APSchedulerIntegration)
        if integration is None or not integration.monitor_jobs:
            return old_submit_job(self, job, run_times, *args, **kwargs)

        listener = None
        with capture_internal_exceptions():
            listener = _begin_check_ins(integration, self, job, run_times)

        try:
            result = old_submit_job(self, job, run_times, *args, **kwargs)
        except MaxInstancesReachedError:
            # The run is skipped, not failed.
            if listener is not None:
                with capture_internal_exceptions():
                    for run_time in run_times:
                        listener.discard(job.id, run_time)
            raise
        except BaseException:
            # Not submitted, so no execution event will close the check-ins.
            if listener is not None:
                with capture_internal_exceptions():
                    for run_time in run_times:
                        listener.finish(job.id, run_time, MonitorStatus.ERROR)
            raise

        if listener is not None:
            with capture_internal_exceptions():
                for run_time in run_times:
                    listener.open(job.id, run_time)

        return result

    BaseExecutor.submit_job = sentry_submit_job
