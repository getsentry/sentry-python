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


def remove_expired_cache_items(options):
    # type: (dict[str, Any]) -> None
    """
    Remove expired cache items from the cache.
    """
    global EXPLAIN_CACHE

    explain_cache_timeout_seconds = options.get(
        "explain_cache_timeout_seconds", EXPLAIN_CACHE_TIMEOUT_SECONDS
    )

    now = datetime.datetime.utcnow()

    for key, statement_timestamp in EXPLAIN_CACHE.items():
        expiration_time = statement_timestamp + datetime.timedelta(
            seconds=explain_cache_timeout_seconds
        )
        expiration_in_the_past = expiration_time < now
        if expiration_in_the_past:
            del EXPLAIN_CACHE[key]


def should_run_explain_plan(statement, options):
    # type: (str, dict[str, Any]) -> bool
    """
    Check cache if the explain plan for the given statement should be run.
    """
    global EXPLAIN_CACHE

    remove_expired_cache_items(options)

    key = hash(statement)
    if key in EXPLAIN_CACHE:
        return False

    explain_cache_size = options.get("explain_cache_size", EXPLAIN_CACHE_SIZE)
    cache_is_full = len(EXPLAIN_CACHE.keys()) >= explain_cache_size
    if cache_is_full:
        return False

    return True
