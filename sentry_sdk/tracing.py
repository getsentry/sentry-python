import re
import uuid

from datetime import datetime

_traceparent_header_format_re = re.compile(
    "^[ \t]*"  # whitespace
    "([0-9a-f]{32})?"  # trace_id
    "-?([0-9a-f]{16})?"  # span_id
    "-?([01])?"  # sampled
    "[ \t]*$"  # whitespace
)


class _EnvironHeaders(object):
    def __init__(self, environ):
        self.environ = environ

    def get(self, key):
        return self.environ.get("HTTP_" + key.replace("-", "_").upper())


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
        trace_id,
        span_id,
        parent_span_id=None,
        same_process_as_parent=True,
        sampled=None,
        transaction=None,
        op=None,
        description=None,
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.same_process_as_parent = same_process_as_parent
        self.sampled = sampled
        self.transaction = transaction
        self._tags = {}
        self._data = {}
        self._finished_spans = []
        self.start_timestamp = datetime.now()
        self.timestamp = None

    def __repr__(self):
        return "<%s(transaction=%r, trace_id=%r, span_id=%r, ref=%r)>" % (
            self.__class__.__name__,
            self.transaction,
            self.trace_id,
            self.span_id,
            self.ref,
        )

    @classmethod
    def start_trace(cls, **kwargs):
        return cls(trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[16:], **kwargs)

    def new_span(self, **kwargs):
        if self.trace_id is None:
            return Span.start_trace()

        rv = Span(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[16:],
            parent_span_id=self.span_id,
            **kwargs
        )
        rv._finished_spans = self._finished_spans
        return rv

    @classmethod
    def continue_from_environ(cls, environ):
        return cls.continue_from_headers(_EnvironHeaders(environ))

    @classmethod
    def continue_from_headers(cls, headers):
        parent = cls.from_traceparent(headers.get("sentry-trace"))
        if parent is None:
            return cls.start_trace()
        return parent.new_span(same_process_as_parent=False)

    def iter_headers(self):
        yield "sentry-trace", self.to_traceparent()

    @classmethod
    def from_traceparent(cls, traceparent):
        if not traceparent:
            return None

        match = _traceparent_header_format_re.match(traceparent)
        if match is None:
            return None

        trace_id, span_id, sampled = match.groups()

        if trace_id is not None:
            trace_id = int(trace_id, 16)
        if span_id is not None:
            span_id = int(span_id, 16)
        if sampled is not None:
            sampled = sampled != "0"

        return cls(trace_id=trace_id, span_id=span_id, sampled=sampled)

    def to_traceparent(self):
        return "%s-%s-%s" % (self.trace_id, self.span_id, "1" if self.sampled else "0")

    def set_tag(self, key, value):
        self._tags[key] = value

    def set_data(self, key, value):
        self._data[key] = value

    def finish(self):
        self.timestamp = datetime.now()
        self._finished_spans.append(self)

    def to_json(self):
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "transaction": self.transaction,
            "tags": self._tags,
            "data": self._data,
            "start_timestamp": self.start_timestamp,
            "timestamp": self.timestamp,
        }

    def get_trace_context(self):
        return {"trace_id": self.trace_id, "span_id": self.span_id}
