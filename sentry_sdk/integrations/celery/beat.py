import sentry_sdk
from sentry_sdk.crons import capture_checkin, MonitorStatus
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.integrations.celery import CeleryIntegration, _now_seconds_since_epoch
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.scope import Scope
from sentry_sdk.utils import (
    ensure_integration_enabled,
    logger,
    match_regex_list,
)

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import Optional
    from typing import Tuple
    from typing import TypeVar
    from typing import Union

    F = TypeVar("F", bound=Callable[..., Any])


try:
    from celery import Task, Celery
    from celery.beat import Scheduler  # type: ignore
    from celery.schedules import crontab, schedule  # type: ignore
    from celery.signals import (  # type: ignore
        task_failure,
        task_success,
        task_retry,
    )
except ImportError:
    raise DidNotEnable("Celery not installed")

try:
    from redbeat.schedulers import RedBeatScheduler  # type: ignore
except ImportError:
    RedBeatScheduler = None


def _get_headers(task):
    # type: (Task) -> Dict[str, Any]
    headers = task.request.get("headers") or {}

    # flatten nested headers
    if "headers" in headers:
        headers.update(headers["headers"])
        del headers["headers"]

    headers.update(task.request.get("properties") or {})

    return headers


def _get_humanized_interval(seconds):
    # type: (float) -> Tuple[int, str]
    TIME_UNITS = (  # noqa: N806
        ("day", 60 * 60 * 24.0),
        ("hour", 60 * 60.0),
        ("minute", 60.0),
    )

    seconds = float(seconds)
    for unit, divider in TIME_UNITS:
        if seconds >= divider:
            interval = int(seconds / divider)
            return (interval, unit)

    return (int(seconds), "second")


def _get_monitor_config(celery_schedule, app, monitor_name):
    # type: (Any, Celery, str) -> Dict[str, Any]
    monitor_config = {}  # type: Dict[str, Any]
    schedule_type = None  # type: Optional[str]
    schedule_value = None  # type: Optional[Union[str, int]]
    schedule_unit = None  # type: Optional[str]

    if isinstance(celery_schedule, crontab):
        schedule_type = "crontab"
        schedule_value = (
            "{0._orig_minute} "
            "{0._orig_hour} "
            "{0._orig_day_of_month} "
            "{0._orig_month_of_year} "
            "{0._orig_day_of_week}".format(celery_schedule)
        )
    elif isinstance(celery_schedule, schedule):
        schedule_type = "interval"
        (schedule_value, schedule_unit) = _get_humanized_interval(
            celery_schedule.seconds
        )

        if schedule_unit == "second":
            logger.warning(
                "Intervals shorter than one minute are not supported by Sentry Crons. Monitor '%s' has an interval of %s seconds. Use the `exclude_beat_tasks` option in the celery integration to exclude it.",
                monitor_name,
                schedule_value,
            )
            return {}

    else:
        logger.warning(
            "Celery schedule type '%s' not supported by Sentry Crons.",
            type(celery_schedule),
        )
        return {}

    monitor_config["schedule"] = {}
    monitor_config["schedule"]["type"] = schedule_type
    monitor_config["schedule"]["value"] = schedule_value

    if schedule_unit is not None:
        monitor_config["schedule"]["unit"] = schedule_unit

    monitor_config["timezone"] = (
        (
            hasattr(celery_schedule, "tz")
            and celery_schedule.tz is not None
            and str(celery_schedule.tz)
        )
        or app.timezone
        or "UTC"
    )

    return monitor_config


def _patch_beat_apply_entry():
    # type: () -> None
    original_apply_entry = Scheduler.apply_entry

    @ensure_integration_enabled(CeleryIntegration, original_apply_entry)
    def sentry_apply_entry(*args, **kwargs):
        # type: (*Any, **Any) -> None
        logger.warn("######  sentry_apply_entry  #######")
        scheduler, schedule_entry = args
        app = scheduler.app

        celery_schedule = schedule_entry.schedule
        monitor_name = schedule_entry.name

        integration = sentry_sdk.get_client().get_integration(CeleryIntegration)
        if match_regex_list(monitor_name, integration.exclude_beat_tasks):
            return original_apply_entry(*args, **kwargs)

        with sentry_sdk.isolation_scope() as scope:
            # When tasks are started from Celery Beat, make sure each task has its own trace.
            logger.warn("#############")
            logger.warn(f"### VORHER {scope}")
            scope.set_new_propagation_context()
            logger.warn(f"### NACHHER {scope}")

            monitor_config = _get_monitor_config(celery_schedule, app, monitor_name)

            is_supported_schedule = bool(monitor_config)
            if is_supported_schedule:
                headers = schedule_entry.options.pop("headers", {})
                headers.update(
                    {
                        "sentry-monitor-slug": monitor_name,
                        "sentry-monitor-config": monitor_config,
                    }
                )

                check_in_id = capture_checkin(
                    monitor_slug=monitor_name,
                    monitor_config=monitor_config,
                    status=MonitorStatus.IN_PROGRESS,
                )
                headers.update({"sentry-monitor-check-in-id": check_in_id})

                # Set the Sentry configuration in the options of the ScheduleEntry.
                # Those will be picked up in `apply_async` and added to the headers.
                schedule_entry.options["headers"] = headers

            return original_apply_entry(*args, **kwargs)

    Scheduler.apply_entry = sentry_apply_entry


def _patch_redbeat_maybe_due():
    # type: () -> None

    if RedBeatScheduler is None:
        return

    original_maybe_due = RedBeatScheduler.maybe_due

    def sentry_maybe_due(*args, **kwargs):
        # type: (*Any, **Any) -> None
        scheduler, schedule_entry = args
        app = scheduler.app

        celery_schedule = schedule_entry.schedule
        monitor_name = schedule_entry.name

        integration = sentry_sdk.get_client().get_integration(CeleryIntegration)
        if integration is None:
            return original_maybe_due(*args, **kwargs)

        if match_regex_list(monitor_name, integration.exclude_beat_tasks):
            return original_maybe_due(*args, **kwargs)

        # When tasks are started from Celery Beat, make sure each task has its own trace.
        scope = Scope.get_isolation_scope()
        scope.set_new_propagation_context()

        monitor_config = _get_monitor_config(celery_schedule, app, monitor_name)

        is_supported_schedule = bool(monitor_config)
        if is_supported_schedule:
            headers = schedule_entry.options.pop("headers", {})
            headers.update(
                {
                    "sentry-monitor-slug": monitor_name,
                    "sentry-monitor-config": monitor_config,
                }
            )

            check_in_id = capture_checkin(
                monitor_slug=monitor_name,
                monitor_config=monitor_config,
                status=MonitorStatus.IN_PROGRESS,
            )
            headers.update({"sentry-monitor-check-in-id": check_in_id})

            # Set the Sentry configuration in the options of the ScheduleEntry.
            # Those will be picked up in `apply_async` and added to the headers.
            schedule_entry.options["headers"] = headers

        return original_maybe_due(*args, **kwargs)

    RedBeatScheduler.maybe_due = sentry_maybe_due


def _setup_celery_beat_signals():
    # type: () -> None
    task_success.connect(crons_task_success)
    task_failure.connect(crons_task_failure)
    task_retry.connect(crons_task_retry)


def crons_task_success(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_success %s", sender)
    headers = _get_headers(sender)

    if "sentry-monitor-slug" not in headers:
        return

    monitor_config = headers.get("sentry-monitor-config", {})

    start_timestamp_s = float(headers["sentry-monitor-start-timestamp-s"])

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        monitor_config=monitor_config,
        check_in_id=headers["sentry-monitor-check-in-id"],
        duration=_now_seconds_since_epoch() - start_timestamp_s,
        status=MonitorStatus.OK,
    )


def crons_task_failure(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_failure %s", sender)
    headers = _get_headers(sender)

    if "sentry-monitor-slug" not in headers:
        return

    monitor_config = headers.get("sentry-monitor-config", {})

    start_timestamp_s = float(headers["sentry-monitor-start-timestamp-s"])

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        monitor_config=monitor_config,
        check_in_id=headers["sentry-monitor-check-in-id"],
        duration=_now_seconds_since_epoch() - start_timestamp_s,
        status=MonitorStatus.ERROR,
    )


def crons_task_retry(sender, **kwargs):
    # type: (Task, Dict[Any, Any]) -> None
    logger.debug("celery_task_retry %s", sender)
    headers = _get_headers(sender)

    if "sentry-monitor-slug" not in headers:
        return

    monitor_config = headers.get("sentry-monitor-config", {})

    start_timestamp_s = float(headers["sentry-monitor-start-timestamp-s"])

    capture_checkin(
        monitor_slug=headers["sentry-monitor-slug"],
        monitor_config=monitor_config,
        check_in_id=headers["sentry-monitor-check-in-id"],
        duration=_now_seconds_since_epoch() - start_timestamp_s,
        status=MonitorStatus.ERROR,
    )
