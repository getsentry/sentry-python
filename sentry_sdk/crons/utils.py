from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Tuple

    from sentry_sdk._types import MonitorConfigSchedule, MonitorConfigScheduleUnit


_INTERVAL_UNITS: "Tuple[Tuple[MonitorConfigScheduleUnit, int], ...]" = (
    ("day", 60 * 60 * 24),
    ("hour", 60 * 60),
    ("minute", 60),
)


def _get_interval_schedule(seconds: float) -> "Optional[MonitorConfigSchedule]":
    """
    Express an interval in the largest unit that divides it evenly.

    Returns None if the interval isn't a whole number of minutes.
    """
    for unit, unit_seconds in _INTERVAL_UNITS:
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            return {
                "type": "interval",
                "value": int(seconds // unit_seconds),
                "unit": unit,
            }
    return None
