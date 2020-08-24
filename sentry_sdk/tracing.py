import re
import uuid
import contextlib
import time

from datetime import datetime, timedelta

import sentry_sdk

from sentry_sdk.utils import capture_internal_exceptions, logger, to_string
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
    from typing import Tuple

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


class _SpanRecorder(object):
    """Limits the number of spans recorded in a transaction."""

    __slots__ = ("maxlen", "spans")

    def __init__(self, maxlen):
        # type: (int) -> None
        # FIXME: this is `maxlen - 1` only to preserve historical behavior
        # enforced by tests.
        # Either this should be changed to `maxlen` or the JS SDK implementation
        # should be changed to match a consistent interpretation of what maxlen
        # limits: either transaction+spans or only child spans.
        self.maxlen = maxlen - 1
        self.spans = []  # type: List[Span]

    def add(self, span):
        # type: (Span) -> None
        if len(self.spans) > self.maxlen:
            span._span_recorder = None
        else:
            self.spans.append(span)


class Span(object):
    __slots__ = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "same_process_as_parent",
        "sampled",
        "op",
        "description",
        "start_timestamp",
        "_start_timestamp_monotonic",
        "status",
        "timestamp",
        "_tags",
        "_data",
        "_span_recorder",
        "hub",
        "_context_manager_state",
    )

    def __new__(cls, **kwargs):
        # type: (**Any) -> Any
        # TODO: consider removing this in a future release.
        # This is for backwards compatibility with releases before Transaction
        # existed, to allow for a smoother transition.
        if "transaction" in kwargs:
            return object.__new__(Transaction)
        return object.__new__(cls)

    def __init__(
        self,
        trace_id=None,  # type: Optional[str]
        span_id=None,  # type: Optional[str]
        parent_span_id=None,  # type: Optional[str]
        same_process_as_parent=True,  # type: bool
        sampled=None,  # type: Optional[bool]
        op=None,  # type: Optional[str]
        description=None,  # type: Optional[str]
        hub=None,  # type: Optional[sentry_sdk.Hub]
        status=None,  # type: Optional[str]
        transaction=None,  # type: Optional[str] # deprecated
    ):
        # type: (...) -> None
        self.trace_id = trace_id or uuid.uuid4().hex
        self.span_id = span_id or uuid.uuid4().hex[16:]
        self.parent_span_id = parent_span_id
        self.same_process_as_parent = same_process_as_parent
        self.sampled = sampled
        self.op = op
        self.description = description
        self.status = status
        self.hub = hub
        self._tags = {}  # type: Dict[str, str]
        self._data = {}  # type: Dict[str, Any]
        self.start_timestamp = datetime.utcnow()
        try:
            # TODO: For Python 3.7+, we could use a clock with ns resolution:
            # self._start_timestamp_monotonic = time.perf_counter_ns()

            # Python 3.3+
            self._start_timestamp_monotonic = time.perf_counter()
        except AttributeError:
            pass

        #: End timestamp of span
        self.timestamp = None  # type: Optional[datetime]

        self._span_recorder = None  # type: Optional[_SpanRecorder]

    def init_span_recorder(self, maxlen):
        # type: (int) -> None
        if self._span_recorder is None:
            self._span_recorder = _SpanRecorder(maxlen)
        self._span_recorder.add(self)

    def __repr__(self):
        # type: () -> str
        return "<%s(trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r)>" % (
            self.__class__.__name__,
            self.trace_id,
            self.span_id,
            self.parent_span_id,
            self.sampled,
        )

    def __enter__(self):
        # type: () -> Span
        hub = self.hub or sentry_sdk.Hub.current

        _, scope = hub._stack[-1]
        old_span = scope.span
        scope.span = self
        self._context_manager_state = (hub, scope, old_span)
        return self

    def __exit__(self, ty, value, tb):
        # type: (Optional[Any], Optional[Any], Optional[Any]) -> None
        if value is not None:
            self.set_status("internal_error")

        hub, scope, old_span = self._context_manager_state
        del self._context_manager_state

        self.finish(hub)
        scope.span = old_span

    def start_child(self, **kwargs):
        # type: (**Any) -> Span
        """
        Start a sub-span from the current span or transaction.

        Takes the same arguments as the initializer of :py:class:`Span`. No
        attributes other than the sample rate are inherited.
        """
        kwargs.setdefault("sampled", self.sampled)

        rv = Span(
            trace_id=self.trace_id, span_id=None, parent_span_id=self.span_id, **kwargs
        )

        rv._span_recorder = recorder = self._span_recorder
        if recorder:
            recorder.add(rv)
        return rv

    def new_span(self, **kwargs):
        # type: (**Any) -> Span
        """Deprecated: use start_child instead."""
        logger.warning("Deprecated: use Span.start_child instead of Span.new_span.")
        return self.start_child(**kwargs)

    @classmethod
    def continue_from_environ(
        cls,
        environ,  # type: typing.Mapping[str, str]
        **kwargs  # type: Any
    ):
        # type: (...) -> Transaction
        if cls is Span:
            logger.warning(
                "Deprecated: use Transaction.continue_from_environ "
                "instead of Span.continue_from_environ."
            )
        return Transaction.continue_from_headers(EnvironHeaders(environ), **kwargs)

    @classmethod
    def continue_from_headers(
        cls,
        headers,  # type: typing.Mapping[str, str]
        **kwargs  # type: Any
    ):
        # type: (...) -> Transaction
        if cls is Span:
            logger.warning(
                "Deprecated: use Transaction.continue_from_headers "
                "instead of Span.continue_from_headers."
            )
        parent = Transaction.from_traceparent(headers.get("sentry-trace"), **kwargs)
        if parent is None:
            parent = Transaction(**kwargs)
        parent.same_process_as_parent = False
        return parent

    def iter_headers(self):
        # type: () -> Generator[Tuple[str, str], None, None]
        yield "sentry-trace", self.to_traceparent()

    @classmethod
    def from_traceparent(
        cls,
        traceparent,  # type: Optional[str]
        **kwargs  # type: Any
    ):
        # type: (...) -> Optional[Transaction]
        if cls is Span:
            logger.warning(
                "Deprecated: use Transaction.from_traceparent "
                "instead of Span.from_traceparent."
            )

        if not traceparent:
            return None

        if traceparent.startswith("00-") and traceparent.endswith("-00"):
            traceparent = traceparent[3:-3]

        match = _traceparent_header_format_re.match(str(traceparent))
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

        return Transaction(
            trace_id=trace_id, parent_span_id=span_id, sampled=sampled, **kwargs
        )

    def to_traceparent(self):
        # type: () -> str
        sampled = ""
        if self.sampled is True:
            sampled = "1"
        if self.sampled is False:
            sampled = "0"
        return "%s-%s-%s" % (self.trace_id, self.span_id, sampled)

    def set_tag(self, key, value):
        # type: (str, Any) -> None
        self._tags[key] = value

    def set_data(self, key, value):
        # type: (str, Any) -> None
        self._data[key] = value

    def set_status(self, value):
        # type: (str) -> None
        self.status = value

    def set_http_status(self, http_status):
        # type: (int) -> None
        self.set_tag("http.status_code", http_status)

        if http_status < 400:
            self.set_status("ok")
        elif 400 <= http_status < 500:
            if http_status == 403:
                self.set_status("permission_denied")
            elif http_status == 404:
                self.set_status("not_found")
            elif http_status == 429:
                self.set_status("resource_exhausted")
            elif http_status == 413:
                self.set_status("failed_precondition")
            elif http_status == 401:
                self.set_status("unauthenticated")
            elif http_status == 409:
                self.set_status("already_exists")
            else:
                self.set_status("invalid_argument")
        elif 500 <= http_status < 600:
            if http_status == 504:
                self.set_status("deadline_exceeded")
            elif http_status == 501:
                self.set_status("unimplemented")
            elif http_status == 503:
                self.set_status("unavailable")
            else:
                self.set_status("internal_error")
        else:
            self.set_status("unknown_error")

    def is_success(self):
        # type: () -> bool
        return self.status == "ok"

    def finish(self, hub=None):
        # type: (Optional[sentry_sdk.Hub]) -> Optional[str]
        # XXX: would be type: (Optional[sentry_sdk.Hub]) -> None, but that leads
        # to incompatible return types for Span.finish and Transaction.finish.
        if self.timestamp is not None:
            # This span is already finished, ignore.
            return None

        hub = hub or self.hub or sentry_sdk.Hub.current

        try:
            duration_seconds = time.perf_counter() - self._start_timestamp_monotonic
            self.timestamp = self.start_timestamp + timedelta(seconds=duration_seconds)
        except AttributeError:
            self.timestamp = datetime.utcnow()

        _maybe_create_breadcrumbs_from_span(hub, self)
        return None

    def to_json(self, client):
        # type: (Optional[sentry_sdk.Client]) -> Dict[str, Any]
        rv = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "same_process_as_parent": self.same_process_as_parent,
            "op": self.op,
            "description": self.description,
            "start_timestamp": self.start_timestamp,
            "timestamp": self.timestamp,
        }  # type: Dict[str, Any]

        if self.status:
            self._tags["status"] = self.status

        tags = self._tags
        if tags:
            rv["tags"] = tags

        data = self._data
        if data:
            rv["data"] = data

        return rv

    def get_trace_context(self):
        # type: () -> Any
        rv = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "op": self.op,
            "description": self.description,
        }
        if self.status:
            rv["status"] = self.status

        return rv


class Transaction(Span):
    __slots__ = ("name",)

    def __init__(
        self,
        name="",  # type: str
        **kwargs  # type: Any
    ):
        # type: (...) -> None
        # TODO: consider removing this in a future release.
        # This is for backwards compatibility with releases before Transaction
        # existed, to allow for a smoother transition.
        if not name and "transaction" in kwargs:
            logger.warning(
                "Deprecated: use Transaction(name=...) to create transactions "
                "instead of Span(transaction=...)."
            )
            name = kwargs.pop("transaction")
        Span.__init__(self, **kwargs)
        self.name = name

    def __repr__(self):
        # type: () -> str
        return (
            "<%s(name=%r, trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r)>"
            % (
                self.__class__.__name__,
                self.name,
                self.trace_id,
                self.span_id,
                self.parent_span_id,
                self.sampled,
            )
        )

    def finish(self, hub=None):
        # type: (Optional[sentry_sdk.Hub]) -> Optional[str]
        if self.timestamp is not None:
            # This transaction is already finished, ignore.
            return None

        if self._span_recorder is None:
            return None

        hub = hub or self.hub or sentry_sdk.Hub.current
        client = hub.client

        if client is None:
            # We have no client and therefore nowhere to send this transaction.
            return None

        if not self.name:
            logger.warning(
                "Transaction has no name, falling back to `<unlabeled transaction>`."
            )
            self.name = "<unlabeled transaction>"

        Span.finish(self, hub)

        if not self.sampled:
            # At this point a `sampled = None` should have already been resolved
            # to a concrete decision.
            if self.sampled is None:
                logger.warning("Discarding transaction without sampling decision.")
            return None

        finished_spans = [
            span.to_json(client)
            for span in self._span_recorder.spans
            if span is not self and span.timestamp is not None
        ]

        return hub.capture_event(
            {
                "type": "transaction",
                "transaction": self.name,
                "contexts": {"trace": self.get_trace_context()},
                "tags": self._tags,
                "timestamp": self.timestamp,
                "start_timestamp": self.start_timestamp,
                "spans": finished_spans,
            }
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


def _maybe_create_breadcrumbs_from_span(hub, span):
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
