import uuid
from datetime import datetime, timedelta

import sentry_sdk
from sentry_sdk._types import MYPY
from sentry_sdk.consts import INSTRUMENTER
from sentry_sdk.tracing import (
    Baggage,
    Transaction,
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
)
from sentry_sdk.tracing.utils import (
    EnvironHeaders,
    extract_sentrytrace_data,
    maybe_create_breadcrumbs_from_span,
)
from sentry_sdk.utils import logger, nanosecond_time


if MYPY:
    import typing
    from typing import Any, Dict, Iterator, List, Optional, Tuple

    import sentry_sdk.profiler


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
        "_start_timestamp_monotonic_ns",
        "status",
        "timestamp",
        "_tags",
        "_data",
        "_span_recorder",
        "hub",
        "_context_manager_state",
        "_containing_transaction",
    )

    def __new__(cls, **kwargs):
        # type: (**Any) -> Any
        """
        Backwards-compatible implementation of Span and Transaction
        creation.
        """

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
        containing_transaction=None,  # type: Optional[Transaction]
        start_timestamp=None,  # type: Optional[datetime]
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
        self._containing_transaction = containing_transaction
        self.start_timestamp = start_timestamp or datetime.utcnow()
        try:
            # profiling depends on this value and requires that
            # it is measured in nanoseconds
            self._start_timestamp_monotonic_ns = nanosecond_time()
        except AttributeError:
            pass

        #: End timestamp of span
        self.timestamp = None  # type: Optional[datetime]

        self._span_recorder = None  # type: Optional[_SpanRecorder]

    # TODO this should really live on the Transaction class rather than the Span
    # class
    def init_span_recorder(self, maxlen):
        # type: (int) -> None
        if self._span_recorder is None:
            self._span_recorder = _SpanRecorder(maxlen)

    def __repr__(self):
        # type: () -> str
        return (
            "<%s(op=%r, description:%r, trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r)>"
            % (
                self.__class__.__name__,
                self.op,
                self.description,
                self.trace_id,
                self.span_id,
                self.parent_span_id,
                self.sampled,
            )
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

    @property
    def containing_transaction(self):
        # type: () -> Optional[Transaction]

        # this is a getter rather than a regular attribute so that transactions
        # can return `self` here instead (as a way to prevent them circularly
        # referencing themselves)
        return self._containing_transaction

    def start_child(self, instrumenter=INSTRUMENTER.SENTRY, **kwargs):
        # type: (str, **Any) -> Span
        """
        Start a sub-span from the current span or transaction.

        Takes the same arguments as the initializer of :py:class:`Span`. The
        trace id, sampling decision, transaction pointer, and span recorder are
        inherited from the current span/transaction.
        """
        hub = self.hub or sentry_sdk.Hub.current
        client = hub.client
        configuration_instrumenter = client and client.options["instrumenter"]

        if instrumenter != configuration_instrumenter:
            return NoOpSpan()

        kwargs.setdefault("sampled", self.sampled)

        child = Span(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
            containing_transaction=self.containing_transaction,
            **kwargs
        )

        span_recorder = (
            self.containing_transaction and self.containing_transaction._span_recorder
        )
        if span_recorder:
            span_recorder.add(child)
        return child

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
        """
        Create a Transaction with the given params, then add in data pulled from
        the 'sentry-trace' and 'baggage' headers from the environ (if any)
        before returning the Transaction.

        This is different from `continue_from_headers` in that it assumes header
        names in the form "HTTP_HEADER_NAME" - such as you would get from a wsgi
        environ - rather than the form "header-name".
        """
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
        """
        Create a transaction with the given params (including any data pulled from
        the 'sentry-trace' and 'baggage' headers).
        """
        # TODO move this to the Transaction class
        if cls is Span:
            logger.warning(
                "Deprecated: use Transaction.continue_from_headers "
                "instead of Span.continue_from_headers."
            )

        # TODO-neel move away from this kwargs stuff, it's confusing and opaque
        # make more explicit
        baggage = Baggage.from_incoming_header(headers.get(BAGGAGE_HEADER_NAME))
        kwargs.update({BAGGAGE_HEADER_NAME: baggage})

        sentrytrace_kwargs = extract_sentrytrace_data(
            headers.get(SENTRY_TRACE_HEADER_NAME)
        )

        if sentrytrace_kwargs is not None:
            kwargs.update(sentrytrace_kwargs)

            # If there's an incoming sentry-trace but no incoming baggage header,
            # for instance in traces coming from older SDKs,
            # baggage will be empty and immutable and won't be populated as head SDK.
            baggage.freeze()

        transaction = Transaction(**kwargs)
        transaction.same_process_as_parent = False

        return transaction

    def iter_headers(self):
        # type: () -> Iterator[Tuple[str, str]]
        """
        Creates a generator which returns the span's `sentry-trace` and `baggage` headers.
        If the span's containing transaction doesn't yet have a `baggage` value,
        this will cause one to be generated and stored.
        """
        yield SENTRY_TRACE_HEADER_NAME, self.to_traceparent()

        if self.containing_transaction:
            baggage = self.containing_transaction.get_baggage().serialize()
            if baggage:
                yield BAGGAGE_HEADER_NAME, baggage

    @classmethod
    def from_traceparent(
        cls,
        traceparent,  # type: Optional[str]
        **kwargs  # type: Any
    ):
        # type: (...) -> Optional[Transaction]
        """
        DEPRECATED: Use Transaction.continue_from_headers(headers, **kwargs)

        Create a Transaction with the given params, then add in data pulled from
        the given 'sentry-trace' header value before returning the Transaction.

        """
        logger.warning(
            "Deprecated: Use Transaction.continue_from_headers(headers, **kwargs) "
            "instead of from_traceparent(traceparent, **kwargs)"
        )

        if not traceparent:
            return None

        return cls.continue_from_headers(
            {SENTRY_TRACE_HEADER_NAME: traceparent}, **kwargs
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
        self.set_tag("http.status_code", str(http_status))

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

    def finish(self, hub=None, end_timestamp=None):
        # type: (Optional[sentry_sdk.Hub], Optional[datetime]) -> Optional[str]
        # XXX: would be type: (Optional[sentry_sdk.Hub]) -> None, but that leads
        # to incompatible return types for Span.finish and Transaction.finish.
        if self.timestamp is not None:
            # This span is already finished, ignore.
            return None

        hub = hub or self.hub or sentry_sdk.Hub.current

        try:
            if end_timestamp:
                self.timestamp = end_timestamp
            else:
                elapsed = nanosecond_time() - self._start_timestamp_monotonic_ns
                self.timestamp = self.start_timestamp + timedelta(
                    microseconds=elapsed / 1000
                )
        except AttributeError:
            self.timestamp = datetime.utcnow()

        maybe_create_breadcrumbs_from_span(hub, self)
        return None

    def to_json(self):
        # type: () -> Dict[str, Any]
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
        }  # type: Dict[str, Any]
        if self.status:
            rv["status"] = self.status

        if self.containing_transaction:
            rv[
                "dynamic_sampling_context"
            ] = self.containing_transaction.get_baggage().dynamic_sampling_context()

        return rv


class NoOpSpan(Span):
    def __repr__(self):
        # type: () -> str
        return self.__class__.__name__

    def start_child(self, instrumenter=INSTRUMENTER.SENTRY, **kwargs):
        # type: (str, **Any) -> NoOpSpan
        return NoOpSpan()

    def new_span(self, **kwargs):
        # type: (**Any) -> NoOpSpan
        pass

    def set_tag(self, key, value):
        # type: (str, Any) -> None
        pass

    def set_data(self, key, value):
        # type: (str, Any) -> None
        pass

    def set_status(self, value):
        # type: (str) -> None
        pass

    def set_http_status(self, http_status):
        # type: (int) -> None
        pass

    def finish(self, hub=None, end_timestamp=None):
        # type: (Optional[sentry_sdk.Hub], Optional[datetime]) -> Optional[str]
        pass
