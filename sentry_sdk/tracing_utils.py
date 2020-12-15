import re
import contextlib
import math

from numbers import Real

import sentry_sdk

from sentry_sdk.utils import (
    capture_internal_exceptions,
    logger,
    to_string,
)
from sentry_sdk._compat import PY2
from sentry_sdk._types import MYPY

if PY2:
    from collections import Mapping
else:
    from collections.abc import Mapping

if MYPY:
    import typing

    from typing import Generator
    from typing import Optional
    from typing import Any
    from typing import Dict

    from sentry_sdk.tracing import Span


SENTRY_TRACE_REGEX = re.compile(
    "^[ \t]*"  # whitespace
    "([0-9a-f]{32})?"  # trace_id
    "-?([0-9a-f]{16})?"  # span_id
    "-?([01])?"  # sampled
    "[ \t]*$"  # whitespace
)


class EnvironHeaders(Mapping):  # type: ignore
    def __init__(
        self,
        environ,  # type: typing.Mapping[str, str]
        prefix="HTTP_",  # type: str
    ):
        # type: (...) -> None
        self.environ = environ
        self.prefix = prefix

    def __getitem__(self, key):
        # type: (str) -> Optional[Any]
        return self.environ[self.prefix + key.replace("-", "_").upper()]

    def __len__(self):
        # type: () -> int
        return sum(1 for _ in iter(self))

    def __iter__(self):
        # type: () -> Generator[str, None, None]
        for k in self.environ:
            if not isinstance(k, str):
                continue

            k = k.replace("-", "_").upper()
            if not k.startswith(self.prefix):
                continue

            yield k[len(self.prefix) :]


def has_tracing_enabled(options):
    # type: (Dict[str, Any]) -> bool
    """
    Returns True if either traces_sample_rate or traces_sampler is
    non-zero/defined, False otherwise.
    """

    return bool(
        options.get("traces_sample_rate") is not None
        or options.get("traces_sampler") is not None
    )


def is_valid_sample_rate(rate):
    # type: (Any) -> bool
    """
    Checks the given sample rate to make sure it is valid type and value (a
    boolean or a number between 0 and 1, inclusive).
    """

    # both booleans and NaN are instances of Real, so a) checking for Real
    # checks for the possibility of a boolean also, and b) we have to check
    # separately for NaN
    if not isinstance(rate, Real) or math.isnan(rate):
        logger.warning(
            "[Tracing] Given sample rate is invalid. Sample rate must be a boolean or a number between 0 and 1. Got {rate} of type {type}.".format(
                rate=rate, type=type(rate)
            )
        )
        return False

    # in case rate is a boolean, it will get cast to 1 if it's True and 0 if it's False
    rate = float(rate)
    if rate < 0 or rate > 1:
        logger.warning(
            "[Tracing] Given sample rate is invalid. Sample rate must be between 0 and 1. Got {rate}.".format(
                rate=rate
            )
        )
        return False

    return True


@contextlib.contextmanager
def record_sql_queries(
    hub,  # type: sentry_sdk.Hub
    cursor,  # type: Any
    query,  # type: Any
    params_list,  # type:  Any
    paramstyle,  # type: Optional[str]
    executemany,  # type: bool
):
    # type: (...) -> Generator[Span, None, None]

    # TODO: Bring back capturing of params by default
    if hub.client and hub.client.options["_experiments"].get(
        "record_sql_params", False
    ):
        if not params_list or params_list == [None]:
            params_list = None

        if paramstyle == "pyformat":
            paramstyle = "format"
    else:
        params_list = None
        paramstyle = None

    query = _format_sql(cursor, query)

    data = {}
    if params_list is not None:
        data["db.params"] = params_list
    if paramstyle is not None:
        data["db.paramstyle"] = paramstyle
    if executemany:
        data["db.executemany"] = True

    with capture_internal_exceptions():
        hub.add_breadcrumb(message=query, category="query", data=data)

    with hub.start_span(op="db", description=query) as span:
        for k, v in data.items():
            span.set_data(k, v)
        yield span


def maybe_create_breadcrumbs_from_span(hub, span):
    # type: (sentry_sdk.Hub, Span) -> None
    if span.op == "redis":
        hub.add_breadcrumb(
            message=span.description, type="redis", category="redis", data=span._tags
        )
    elif span.op == "http":
        hub.add_breadcrumb(type="http", category="httplib", data=span._data)
    elif span.op == "subprocess":
        hub.add_breadcrumb(
            type="subprocess",
            category="subprocess",
            message=span.description,
            data=span._data,
        )


def _format_sql(cursor, sql):
    # type: (Any, str) -> Optional[str]

    real_sql = None

    # If we're using psycopg2, it could be that we're
    # looking at a query that uses Composed objects. Use psycopg2's mogrify
    # function to format the query. We lose per-parameter trimming but gain
    # accuracy in formatting.
    try:
        if hasattr(cursor, "mogrify"):
            real_sql = cursor.mogrify(sql)
            if isinstance(real_sql, bytes):
                real_sql = real_sql.decode(cursor.connection.encoding)
    except Exception:
        real_sql = None

    return real_sql or to_string(sql)
