import random
from datetime import datetime

import sentry_sdk
from sentry_sdk._types import MYPY
from sentry_sdk.tracing import TRANSACTION_SOURCE_CUSTOM
from sentry_sdk.tracing.span import Span
from sentry_sdk.tracing.utils import has_tracing_enabled, is_valid_sample_rate
from sentry_sdk.utils import logger


if MYPY:
    from typing import Any, Dict, Optional

    import sentry_sdk.profiler
    from sentry_sdk._types import Event, MeasurementUnit, SamplingContext
    from sentry_sdk.tracing import Baggage


class Transaction(Span):
    __slots__ = (
        "name",
        "source",
        "parent_sampled",
        # used to create baggage value for head SDKs in dynamic sampling
        "sample_rate",
        "_measurements",
        "_contexts",
        "_profile",
        "_baggage",
    )

    def __init__(
        self,
        name="",  # type: str
        parent_sampled=None,  # type: Optional[bool]
        baggage=None,  # type: Optional[Baggage]
        source=TRANSACTION_SOURCE_CUSTOM,  # type: str
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
        self.source = source
        self.sample_rate = None  # type: Optional[float]
        self.parent_sampled = parent_sampled
        self._measurements = {}  # type: Dict[str, Any]
        self._contexts = {}  # type: Dict[str, Any]
        self._profile = None  # type: Optional[sentry_sdk.profiler.Profile]
        self._baggage = baggage

    def __repr__(self):
        # type: () -> str
        return (
            "<%s(name=%r, op=%r, trace_id=%r, span_id=%r, parent_span_id=%r, sampled=%r, source=%r)>"
            % (
                self.__class__.__name__,
                self.name,
                self.op,
                self.trace_id,
                self.span_id,
                self.parent_span_id,
                self.sampled,
                self.source,
            )
        )

    def __enter__(self):
        # type: () -> Transaction
        super(Transaction, self).__enter__()

        if self._profile is not None:
            self._profile.__enter__()

        return self

    def __exit__(self, ty, value, tb):
        # type: (Optional[Any], Optional[Any], Optional[Any]) -> None
        if self._profile is not None:
            self._profile.__exit__(ty, value, tb)

        super(Transaction, self).__exit__(ty, value, tb)

    @property
    def containing_transaction(self):
        # type: () -> Transaction

        # Transactions (as spans) belong to themselves (as transactions). This
        # is a getter rather than a regular attribute to avoid having a circular
        # reference.
        return self

    def finish(self, hub=None, end_timestamp=None):
        # type: (Optional[sentry_sdk.Hub], Optional[datetime]) -> Optional[str]
        if self.timestamp is not None:
            # This transaction is already finished, ignore.
            return None

        hub = hub or self.hub or sentry_sdk.Hub.current
        client = hub.client

        if client is None:
            # We have no client and therefore nowhere to send this transaction.
            return None

        # This is a de facto proxy for checking if sampled = False
        if self._span_recorder is None:
            logger.debug("Discarding transaction because sampled = False")

            # This is not entirely accurate because discards here are not
            # exclusively based on sample rate but also traces sampler, but
            # we handle this the same here.
            if client.transport and has_tracing_enabled(client.options):
                client.transport.record_lost_event(
                    "sample_rate", data_category="transaction"
                )

            return None

        if not self.name:
            logger.warning(
                "Transaction has no name, falling back to `<unlabeled transaction>`."
            )
            self.name = "<unlabeled transaction>"

        Span.finish(self, hub, end_timestamp)

        if not self.sampled:
            # At this point a `sampled = None` should have already been resolved
            # to a concrete decision.
            if self.sampled is None:
                logger.warning("Discarding transaction without sampling decision.")

            return None

        finished_spans = [
            span.to_json()
            for span in self._span_recorder.spans
            if span.timestamp is not None
        ]

        # we do this to break the circular reference of transaction -> span
        # recorder -> span -> containing transaction (which is where we started)
        # before either the spans or the transaction goes out of scope and has
        # to be garbage collected
        self._span_recorder = None

        contexts = {}
        contexts.update(self._contexts)
        contexts.update({"trace": self.get_trace_context()})

        event = {
            "type": "transaction",
            "transaction": self.name,
            "transaction_info": {"source": self.source},
            "contexts": contexts,
            "tags": self._tags,
            "timestamp": self.timestamp,
            "start_timestamp": self.start_timestamp,
            "spans": finished_spans,
        }  # type: Event

        if self._profile is not None and self._profile.valid():
            event["profile"] = self._profile
            contexts.update({"profile": self._profile.get_profile_context()})
            self._profile = None

        event["measurements"] = self._measurements

        return hub.capture_event(event)

    def set_measurement(self, name, value, unit=""):
        # type: (str, float, MeasurementUnit) -> None
        self._measurements[name] = {"value": value, "unit": unit}

    def set_context(self, key, value):
        # type: (str, Any) -> None
        self._contexts[key] = value

    def to_json(self):
        # type: () -> Dict[str, Any]
        rv = super(Transaction, self).to_json()

        rv["name"] = self.name
        rv["source"] = self.source
        rv["sampled"] = self.sampled

        return rv

    def get_baggage(self):
        # type: () -> Baggage
        """
        The first time a new baggage with sentry items is made,
        it will be frozen.
        """
        if not self._baggage or self._baggage.mutable:
            self._baggage = Baggage.populate_from_transaction(self)

        return self._baggage

    def _set_initial_sampling_decision(self, sampling_context):
        # type: (SamplingContext) -> None
        """
        Sets the transaction's sampling decision, according to the following
        precedence rules:

        1. If a sampling decision is passed to `start_transaction`
        (`start_transaction(name: "my transaction", sampled: True)`), that
        decision will be used, regardless of anything else

        2. If `traces_sampler` is defined, its decision will be used. It can
        choose to keep or ignore any parent sampling decision, or use the
        sampling context data to make its own decision or to choose a sample
        rate for the transaction.

        3. If `traces_sampler` is not defined, but there's a parent sampling
        decision, the parent sampling decision will be used.

        4. If `traces_sampler` is not defined and there's no parent sampling
        decision, `traces_sample_rate` will be used.
        """

        hub = self.hub or sentry_sdk.Hub.current
        client = hub.client
        options = (client and client.options) or {}
        transaction_description = "{op}transaction <{name}>".format(
            op=("<" + self.op + "> " if self.op else ""), name=self.name
        )

        # nothing to do if there's no client or if tracing is disabled
        if not client or not has_tracing_enabled(options):
            self.sampled = False
            return

        # if the user has forced a sampling decision by passing a `sampled`
        # value when starting the transaction, go with that
        if self.sampled is not None:
            self.sample_rate = float(self.sampled)
            return

        # we would have bailed already if neither `traces_sampler` nor
        # `traces_sample_rate` were defined, so one of these should work; prefer
        # the hook if so
        sample_rate = (
            options["traces_sampler"](sampling_context)
            if callable(options.get("traces_sampler"))
            else (
                # default inheritance behavior
                sampling_context["parent_sampled"]
                if sampling_context["parent_sampled"] is not None
                else options["traces_sample_rate"]
            )
        )

        # Since this is coming from the user (or from a function provided by the
        # user), who knows what we might get. (The only valid values are
        # booleans or numbers between 0 and 1.)
        if not is_valid_sample_rate(sample_rate):
            logger.warning(
                "[Tracing] Discarding {transaction_description} because of invalid sample rate.".format(
                    transaction_description=transaction_description,
                )
            )
            self.sampled = False
            return

        self.sample_rate = float(sample_rate)

        # if the function returned 0 (or false), or if `traces_sample_rate` is
        # 0, it's a sign the transaction should be dropped
        if not sample_rate:
            logger.debug(
                "[Tracing] Discarding {transaction_description} because {reason}".format(
                    transaction_description=transaction_description,
                    reason=(
                        "traces_sampler returned 0 or False"
                        if callable(options.get("traces_sampler"))
                        else "traces_sample_rate is set to 0"
                    ),
                )
            )
            self.sampled = False
            return

        # Now we roll the dice. random.random is inclusive of 0, but not of 1,
        # so strict < is safe here. In case sample_rate is a boolean, cast it
        # to a float (True becomes 1.0 and False becomes 0.0)
        self.sampled = random.random() < float(sample_rate)

        if self.sampled:
            logger.debug(
                "[Tracing] Starting {transaction_description}".format(
                    transaction_description=transaction_description,
                )
            )
        else:
            logger.debug(
                "[Tracing] Discarding {transaction_description} because it's not included in the random sample (sampling rate = {sample_rate})".format(
                    transaction_description=transaction_description,
                    sample_rate=float(sample_rate),
                )
            )
