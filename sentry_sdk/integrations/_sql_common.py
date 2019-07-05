# -*- coding: utf-8 -*-
from __future__ import absolute_import

from sentry_sdk.utils import format_and_strip, safe_repr

if False:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Optional


class _FormatConverter(object):
    def __init__(self, param_mapping):
        # type: (Dict[str, int]) -> None

        self.param_mapping = param_mapping
        self.params = []  # type: List[Any]

    def __getitem__(self, val):
        # type: (str) -> str
        self.params.append(self.param_mapping.get(val))
        return "%s"


def _format_sql_impl(sql, params):
    # type: (Any, Any) -> Tuple[str, List[str]]
    rv = []

    if isinstance(params, dict):
        # convert sql with named parameters to sql with unnamed parameters
        conv = _FormatConverter(params)
        if params:
            sql = sql % conv
            params = conv.params
        else:
            params = ()

    for param in params or ():
        if param is None:
            rv.append("NULL")
        param = safe_repr(param)
        rv.append(param)

    return sql, rv


def format_sql(sql, params, cursor):
    # type: (str, List[Any], Any) -> Optional[str]

    real_sql = None
    real_params = None

    try:
        # Prefer our own SQL formatting logic because it's the only one that
        # has proper value trimming.
        real_sql, real_params = _format_sql_impl(sql, params)
        if real_sql:
            real_sql = format_and_strip(real_sql, real_params)
    except Exception:
        pass

    if not real_sql and hasattr(cursor, "mogrify"):
        # If formatting failed and we're using psycopg2, it could be that we're
        # looking at a query that uses Composed objects. Use psycopg2's mogrify
        # function to format the query. We lose per-parameter trimming but gain
        # accuracy in formatting.
        #
        # This is intentionally the second choice because we assume Composed
        # queries are not widely used, while per-parameter trimming is
        # generally highly desirable.
        try:
            if hasattr(cursor, "mogrify"):
                real_sql = cursor.mogrify(sql, params)
                if isinstance(real_sql, bytes):
                    real_sql = real_sql.decode(cursor.connection.encoding)
        except Exception:
            pass

    return real_sql or None
