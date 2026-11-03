from datetime import timedelta, timezone
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import tzinfo
    from typing import FrozenSet, Optional, Set, Tuple

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


@lru_cache(maxsize=None)
def _get_iana_timezone_names() -> "FrozenSet[str]":
    names: "Set[str]" = set()
    try:
        from zoneinfo import available_timezones

        names.update(available_timezones())
    except ImportError:
        try:
            from backports.zoneinfo import (  # type: ignore[import-not-found, no-redef]
                available_timezones,
            )

            names.update(available_timezones())
        except ImportError:
            pass

    try:
        import pytz  # type: ignore[import-untyped]

        # pytz's lazy set looks empty to set.update until it's iterated.
        names.update(iter(pytz.all_timezones_set))
    except ImportError:
        pass

    # Some systems keep these files in the zoneinfo directory, but they aren't zones.
    names.difference_update(("localtime", "posixrules"))
    return frozenset(names)


def _get_timezone_name(tz: "tzinfo") -> "Optional[str]":
    """
    Return the IANA name of the timezone, or None if it has none.
    """
    # zoneinfo and pytz. tzlocal can give a zone a non-IANA key, like "local"
    # or the basename of a TZ file path.
    name = getattr(tz, "key", None) or getattr(tz, "zone", None)
    if isinstance(name, str):
        return name if name in _get_iana_timezone_names() else None

    if isinstance(tz, timezone):
        offset = tz.utcoffset(None)
        if offset % timedelta(hours=1):
            return None
        hours = offset // timedelta(hours=1)
        return "Etc/GMT%+d" % -hours if hours else "UTC"

    return None
