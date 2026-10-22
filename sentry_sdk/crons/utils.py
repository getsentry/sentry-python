from datetime import timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import tzinfo
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


def _get_timezone_name(tz: "tzinfo") -> "Optional[str]":
    """
    Return the IANA name of the timezone, or None if it has none.
    """
    # zoneinfo and pytz
    name = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if isinstance(name, str):
        # tzlocal's zone when it can't find the system's.
        return None if name == "local" else name

    if isinstance(tz, timezone):
        offset = tz.utcoffset(None)
        if offset % timedelta(hours=1):
            return None
        hours = offset // timedelta(hours=1)
        return "Etc/GMT%+d" % -hours if hours else "UTC"

    return None
