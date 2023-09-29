import datetime

from sentry_sdk.consts import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

EXPLAIN_CACHE = {}

EXPLAIN_CACHE_SIZE = 50

EXPLAIN_CACHE_TIMEOUT_SECONDS = 60 * 60 * 24


def cache_statement(statement):
    # type: (str) -> None
    global EXPLAIN_CACHE
    EXPLAIN_CACHE[hash(statement)] = datetime.datetime.utcnow()


def should_run_explain_plan(statement, options):
    # type: (str, dict[str, Any]) -> bool
    """
    Check cache if the explain plan for the given statement should be run.
    """
    explain_cache_size = options.get("explain_cache_size", EXPLAIN_CACHE_SIZE)
    explain_cache_timeout_seconds = options.get(
        "explain_cache_timeout_seconds", EXPLAIN_CACHE_TIMEOUT_SECONDS
    )

    now = datetime.datetime.utcnow()
    key = hash(statement)

    global EXPLAIN_CACHE

    if key in EXPLAIN_CACHE:
        statement_timestamp = EXPLAIN_CACHE[key]

        # Cached item expired, remove from cache
        if (
            statement_timestamp
            + datetime.timedelta(seconds=explain_cache_timeout_seconds)
            < now
        ):
            del EXPLAIN_CACHE[key]

        return False

    if len(EXPLAIN_CACHE.keys()) >= explain_cache_size:
        return False

    return True
