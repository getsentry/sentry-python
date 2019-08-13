import re
import uuid
import contextlib

from datetime import datetime

from sentry_sdk.utils import capture_internal_exceptions
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
    from typing import List

    import sentry_sdk

_traceparent_header_format_re = re.compile(
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
        return self.environ[self.prefix + key.replace("-", "_").upper()]

    def __len__(self):
        return sum(1 for _ in iter(self))

    def __iter__(self):
        for k in self.environ:
            if not isinstance(k, str):
                continue

            k = k.replace("-", "_").upper()
            if not k.startswith(self.prefix):
                continue

            yield k[len(self.prefix) :]


class Span(object):
    __slots__ = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "same_process_as_parent",
        "sampled",
        "transaction",
        "op",
        "description",
        "start_timestamp",
        "timestamp",
        "_tags",
        "_data",
        "_finished_spans",
    )

    def __init__(
        self,
        trace_id=None,  # type: Optional[str]
        span_id=None,  # type: Optional[str]
        parent_span_id=None,  # type: Optional[str]
        same_process_as_parent=True,  # type: bool
        sampled=None,  # type: Optional[bool]
        transaction=None,  # type: Optional[str]
        op=None,  # type: Optional[str]
        description=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        self.trace_id = trace_id or uuid.uuid4().hex
        self.span_id = span_id or uuid.uuid4().hex[16:]
        self.parent_span_id = parent_span_id
        self.same_process_as_parent = same_process_as_parent
        self.sampled = sampled
        self.transaction = transaction
        self.op = op
        self.description = description
        self._tags = {}  # type: Dict[str, str]
        self._data = {}  # type: Dict[str, Any]
        self._finished_spans = None  # type: Optional[List[Span]]
        self.start_timestamp = datetime.now()

        #: End timestamp of span
        self.timestamp = None

    def init_finished_spans(self):
        if self._finished_spans is None:
            self._finished_spans = []

    def __repr__(self):
        return (
            "<%s(transaction=%r, trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r)>"
            % (
                self.__class__.__name__,
                self.transaction,
                self.trace_id,
                self.span_id,
                self.parent_span_id,
                self.sampled,
            )
        )

    def new_span(self, **kwargs):
        rv = type(self)(
            trace_id=self.trace_id,
            span_id=None,
            parent_span_id=self.span_id,
            sampled=self.sampled,
            **kwargs
        )
        rv._finished_spans = self._finished_spans
        return rv

    @classmethod
    def continue_from_environ(cls, environ):
        return cls.continue_from_headers(EnvironHeaders(environ))

    @classmethod
    def continue_from_headers(cls, headers):
        parent = cls.from_traceparent(headers.get("sentry-trace"))
        if parent is None:
            return cls()
        return parent.new_span(same_process_as_parent=False)

    def iter_headers(self):
        yield "sentry-trace", self.to_traceparent()

    @classmethod
    def from_traceparent(cls, traceparent):
        if not traceparent:
            return None

        if traceparent.startswith("00-") and traceparent.endswith("-00"):
            traceparent = traceparent[3:-3]

        match = _traceparent_header_format_re.match(traceparent)
        if match is None:
            return None

        trace_id, span_id, sampled_str = match.groups()

        if trace_id is not None:
            trace_id = "{:032x}".format(int(trace_id, 16))
        if span_id is not None:
            span_id = "{:016x}".format(int(span_id, 16))

        if sampled_str:
            sampled = sampled_str != "0"  # type: Optional[bool]
        else:
            sampled = None

        return cls(trace_id=trace_id, span_id=span_id, sampled=sampled)

    def to_traceparent(self):
        sampled = ""
        if self.sampled is True:
            sampled = "1"
        if self.sampled is False:
            sampled = "0"
        return "%s-%s-%s" % (self.trace_id, self.span_id, sampled)

    def to_legacy_traceparent(self):
        return "00-%s-%s-00" % (self.trace_id, self.span_id)

    def set_tag(self, key, value):
        self._tags[key] = value

    def set_data(self, key, value):
        self._data[key] = value

    def set_failure(self):
        self.set_tag("status", "failure")

    def set_success(self):
        self.set_tag("status", "success")

    def is_success(self):
        return self._tags.get("status") in (None, "success")

    def finish(self):
        self.timestamp = datetime.now()
        if self._finished_spans is not None:
            self._finished_spans.append(self)

    def to_json(self):
        rv = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "same_process_as_parent": self.same_process_as_parent,
            "transaction": self.transaction,
            "op": self.op,
            "description": self.description,
            "start_timestamp": self.start_timestamp,
            "timestamp": self.timestamp,
            "tags": self._tags,
            "data": self._data,
        }

        return rv

    def get_trace_context(self):
        rv = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "op": self.op,
            "description": self.description,
        }

        if "status" in self._tags:
            rv["status"] = self._tags["status"]

        return rv


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

    return real_sql or str(sql)


@contextlib.contextmanager
def record_sql_queries(
    hub,  # type: sentry_sdk.Hub
    cursor,  # type: Any
    query,  # type: Any
    params_list,  # type:  Any
    paramstyle,  # type: Optional[str]
    executemany,  # type: bool
):
    # type: (...) -> Generator[Optional[Span], None, None]
    if not params_list or params_list == [None]:
        params_list = None

    if paramstyle == "pyformat":
        paramstyle = "format"

    query = _format_sql(cursor, query)

    data = {"db.params": params_list, "db.paramstyle": paramstyle}
    if executemany:
        data["db.executemany"] = True

    with capture_internal_exceptions():
        hub.add_breadcrumb(message=query, category="query", data=data)

    with hub.span(op="db", description=query) as span:
        for k, v in data.items():
            span.set_data(k, v)
        yield span


@contextlib.contextmanager
def record_http_request(hub, url, method):
    data_dict = {"url": url, "method": method}

    with hub.span(op="http", description="%s %s" % (url, method)) as span:
        try:
            yield data_dict
        finally:
            if span is not None:
                if "status_code" in data_dict:
                    span.set_tag("http.status_code", data_dict["status_code"])
                for k, v in data_dict.items():
                    span.set_data(k, v)


def maybe_create_breadcrumbs_from_span(hub, span):
    if span.op == "redis":
        hub.add_breadcrumb(type="redis", category="redis", data=span._tags)
    elif span.op == "http" and span.is_success():
        hub.add_breadcrumb(
            type="http",
            category="httplib",
            data=span._data,
            hint={"httplib_response": span._data.get("httplib_response")},
        )
    elif span.op == "subprocess":
        hub.add_breadcrumb(
            type="subprocess",
            category="subprocess",
            data=span._data,
            hint={"popen_instance": span._data.get("popen_instance")},
        )
