import re
import uuid

_traceparent_header_format_re = re.compile(
    "^[ \t]*([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})" "(-.*)?[ \t]*$"
)


class _EnvironHeaders(object):
    def __init__(self, environ):
        self.environ = environ

    def get(self, key):
        return self.environ.get("HTTP_" + key.replace("-", "_").upper())


class SpanContext(object):
    def __init__(self, trace_id, span_id, recorded=False, parent=None):
        self.trace_id = trace_id
        self.span_id = span_id
        self.recorded = recorded
        self.parent = None

    def __repr__(self):
        return "%s(trace_id=%r, span_id=%r, recorded=%r)" % (
            self.__class__.__name__,
            self.trace_id,
            self.span_id,
            self.recorded,
        )

    @classmethod
    def start_trace(cls, recorded=False):
        return cls(
            trace_id=uuid.uuid4().hex, span_id=uuid.uuid4().hex[16:], recorded=recorded
        )

    def new_span(self):
        if self.trace_id is None:
            return SpanContext.start_trace()
        return SpanContext(
            trace_id=self.trace_id,
            span_id=uuid.uuid4().hex[16:],
            parent=self,
            recorded=self.recorded,
        )

    @classmethod
    def continue_from_environ(cls, environ):
        return cls.continue_from_headers(_EnvironHeaders(environ))

    @classmethod
    def continue_from_headers(cls, headers):
        parent = cls.from_traceparent(headers.get("sentry-trace"))
        if parent is None:
            return cls.start_trace()
        return parent.new_span()

    def iter_headers(self):
        yield "sentry-trace", self.to_traceparent()

    @classmethod
    def from_traceparent(cls, traceparent):
        if not traceparent:
            return None

        match = _traceparent_header_format_re.match(traceparent)
        if match is None:
            return None

        version, trace_id, span_id, trace_options, extra = match.groups()

        if int(trace_id, 16) == 0 or int(span_id, 16) == 0:
            return None

        version = int(version, 16)
        if version == 0:
            if extra:
                return None
        elif version == 255:
            return None

        options = int(trace_options, 16)

        return cls(trace_id=trace_id, span_id=span_id, recorded=options & 1 != 0)

    def to_traceparent(self):
        return "%02x-%s-%s-%02x" % (
            0,
            self.trace_id,
            self.span_id,
            self.recorded and 1 or 0,
        )
