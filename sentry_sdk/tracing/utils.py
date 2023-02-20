import contextlib
import math
from decimal import Decimal
from numbers import Real

import sentry_sdk
from sentry_sdk._compat import PY2
from sentry_sdk._types import MYPY
from sentry_sdk.consts import OP
from sentry_sdk.tracing.consts import SENTRY_TRACE_REGEX
from sentry_sdk.utils import capture_internal_exceptions, logger, to_string


if PY2:
    from collections import Mapping
else:
    from collections.abc import Mapping


if MYPY:
    import typing
    from typing import Any, Dict, Generator, Optional, Union

    from sentry_sdk.tracing.span import Span


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
    defined and enable_tracing is set and not false.
    """
    return bool(
        options.get("enable_tracing") is not False
        and (
            options.get("traces_sample_rate") is not None
            or options.get("traces_sampler") is not None
        )
    )


def is_valid_sample_rate(rate):
    # type: (Any) -> bool
    """
    Checks the given sample rate to make sure it is valid type and value (a
    boolean or a number between 0 and 1, inclusive).
    """

    # both booleans and NaN are instances of Real, so a) checking for Real
    # checks for the possibility of a boolean also, and b) we have to check
    # separately for NaN and Decimal does not derive from Real so need to check that too
    if not isinstance(rate, (Real, Decimal)) or math.isnan(rate):
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

    with hub.start_span(op=OP.DB, description=query) as span:
        for k, v in data.items():
            span.set_data(k, v)
        yield span


def maybe_create_breadcrumbs_from_span(hub, span):
    # type: (sentry_sdk.Hub, Span) -> None
    if span.op == OP.DB_REDIS:
        hub.add_breadcrumb(
            message=span.description, type="redis", category="redis", data=span._tags
        )
    elif span.op == OP.HTTP_CLIENT:
        hub.add_breadcrumb(type="http", category="httplib", data=span._data)
    elif span.op == "subprocess":
        hub.add_breadcrumb(
            type="subprocess",
            category="subprocess",
            message=span.description,
            data=span._data,
        )


def extract_sentrytrace_data(header):
    # type: (Optional[str]) -> Optional[typing.Mapping[str, Union[str, bool, None]]]
    """
    Given a `sentry-trace` header string, return a dictionary of data.
    """
    if not header:
        return None

    if header.startswith("00-") and header.endswith("-00"):
        header = header[3:-3]

    match = SENTRY_TRACE_REGEX.match(header)
    if not match:
        return None

    trace_id, parent_span_id, sampled_str = match.groups()
    parent_sampled = None

    if trace_id:
        trace_id = "{:032x}".format(int(trace_id, 16))
    if parent_span_id:
        parent_span_id = "{:016x}".format(int(parent_span_id, 16))
    if sampled_str:
        parent_sampled = sampled_str != "0"

    return {
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "parent_sampled": parent_sampled,
    }


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
