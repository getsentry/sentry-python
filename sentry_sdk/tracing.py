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
        "ref",
        "ref_type",
        "sampled",
        "transaction",
        "_tags",
        "_finished_spans",
        "start",
        "end",
    )

    def __init__(
        self, trace_id, span_id, transaction=None, ref=None, ref_type=None, sampled=None
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.ref = ref
        self.ref_type = ref_type
        self.sampled = sampled
        self.transaction = transaction
        self._tags = {}
        self._finished_spans = []
        self.start = datetime.now()
        self.end = None

    def __repr__(self):
        return "<%s(transaction=%r, trace_id=%r, span_id=%r, ref=%r)>" % (
            self.__class__.__name__,
            self.transaction,
            self.trace_id,
            self.span_id,
            self.ref,
        )

    @classmethod
    def start_trace(cls, transaction=None, sampled=None):
        return cls(
            transaction=transaction,
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[16:],
            sampled=sampled,
        )

    def new_span(self, ref_type="child"):
        if self.trace_id is None:
            return Span.start_trace()

        rv = Span(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[16:],
            ref=self,
            ref_type=ref_type,
            sampled=self.sampled,
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
        return parent.new_span("follows_from")

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
            sampled = sampled == "1"

        return cls(trace_id=trace_id, span_id=span_id, sampled=sampled)

    def to_traceparent(self):
        return "%s-%s-%s" % (
            self.trace_id,
            self.span_id,
            "1" if not self.sampled else "0",
        )

    def set_tag(self, key, value):
        self._tags[key] = value

    def finish(self):
        self.end = datetime.now()
        self._finished_spans.append(self)

    def to_json(self):
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "ref_span_id": self.ref and self.ref.span_id or None,
            "transaction": self.transaction,
            "tags": self._tags,
            "start": self.start,
            "end": self.end,
        }
