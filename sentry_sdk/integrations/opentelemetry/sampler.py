import random
from typing import cast

from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace.span import TraceState

import sentry_sdk
from sentry_sdk.tracing_utils import has_tracing_enabled
from sentry_sdk.utils import is_valid_sample_rate, logger
from sentry_sdk.integrations.opentelemetry.consts import (
    TRACESTATE_SAMPLED_KEY,
    TRACESTATE_SAMPLE_RATE_KEY,
    SentrySpanAttribute,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional, Sequence, Union
    from opentelemetry.context import Context
    from opentelemetry.trace import Link, SpanKind
    from opentelemetry.trace.span import SpanContext
    from opentelemetry.util.types import Attributes


def get_parent_sampled(parent_context, trace_id):
    # type: (Optional[SpanContext], int) -> Optional[bool]
    if parent_context is None:
        return None

    is_span_context_valid = parent_context is not None and parent_context.is_valid

    # Only inherit sample rate if `traceId` is the same
    if is_span_context_valid and parent_context.trace_id == trace_id:
        # this is getSamplingDecision in JS
        # if there was no sampling flag, defer the decision
        dsc_sampled = parent_context.trace_state.get(TRACESTATE_SAMPLED_KEY)
        if dsc_sampled == "deferred":
            return None

        if parent_context.trace_flags.sampled is not None:
            return parent_context.trace_flags.sampled

        if dsc_sampled == "true":
            return True
        elif dsc_sampled == "false":
            return False

    return None


def get_parent_sample_rate(parent_context, trace_id):
    # type: (Optional[SpanContext], int) -> Optional[float]
    if parent_context is None:
        return None

    is_span_context_valid = parent_context is not None and parent_context.is_valid

    if is_span_context_valid and parent_context.trace_id == trace_id:
        parent_sample_rate = parent_context.trace_state.get(TRACESTATE_SAMPLE_RATE_KEY)
        if parent_sample_rate is None:
            return None

        try:
            return float(parent_sample_rate)
        except Exception:
            return None

    return None


def dropped_result(parent_span_context, attributes, sample_rate=None):
    # type: (SpanContext, Attributes, Optional[float]) -> SamplingResult
    # these will only be added the first time in a root span sampling decision
    # if sample_rate is provided, it'll be updated in trace state
    trace_state = parent_span_context.trace_state

    if TRACESTATE_SAMPLED_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLED_KEY, "false")
    elif trace_state.get(TRACESTATE_SAMPLED_KEY) == "deferred":
        trace_state = trace_state.update(TRACESTATE_SAMPLED_KEY, "false")

    if sample_rate is not None:
        trace_state = trace_state.update(TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate))

    is_root_span = not (
        parent_span_context.is_valid and not parent_span_context.is_remote
    )
    if is_root_span:
        # Tell Sentry why we dropped the transaction/root-span
        client = sentry_sdk.get_client()
        if client.monitor and client.monitor.downsample_factor > 0:
            reason = "backpressure"
        else:
            reason = "sample_rate"

        if client.transport and has_tracing_enabled(client.options):
            client.transport.record_lost_event(reason, data_category="transaction")

            # Only one span (the transaction itself) is discarded, since we did not record any spans here.
            client.transport.record_lost_event(reason, data_category="span")

    return SamplingResult(
        Decision.DROP,
        attributes=attributes,
        trace_state=trace_state,
    )


def sampled_result(span_context, attributes, sample_rate):
    # type: (SpanContext, Attributes, Optional[float]) -> SamplingResult
    # these will only be added the first time in a root span sampling decision
    # if sample_rate is provided, it'll be updated in trace state
    trace_state = span_context.trace_state

    if TRACESTATE_SAMPLED_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLED_KEY, "true")
    elif trace_state.get(TRACESTATE_SAMPLED_KEY) == "deferred":
        trace_state = trace_state.update(TRACESTATE_SAMPLED_KEY, "true")

    if sample_rate is not None:
        trace_state = trace_state.update(TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate))

    return SamplingResult(
        Decision.RECORD_AND_SAMPLE,
        attributes=attributes,
        trace_state=trace_state,
    )


class SentrySampler(Sampler):
    def should_sample(
        self,
        parent_context,  # type: Optional[Context]
        trace_id,  # type: int
        name,  # type: str
        kind=None,  # type: Optional[SpanKind]
        attributes=None,  # type: Attributes
        links=None,  # type: Optional[Sequence[Link]]
        trace_state=None,  # type: Optional[TraceState]
    ):
        # type: (...) -> SamplingResult
        client = sentry_sdk.get_client()

        parent_span_context = trace.get_current_span(parent_context).get_span_context()

        attributes = attributes or {}

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return dropped_result(parent_span_context, attributes)

        # parent_span_context.is_valid means this span has a parent, remote or local
        is_root_span = not parent_span_context.is_valid or parent_span_context.is_remote

        sample_rate = None

        # Explicit sampled value provided at start_span
        custom_sampled = cast(
            "Optional[bool]", attributes.get(SentrySpanAttribute.CUSTOM_SAMPLED)
        )
        if custom_sampled is not None:
            if is_root_span:
                sample_rate = float(custom_sampled)
                if sample_rate > 0:
                    return sampled_result(
                        parent_span_context, attributes, sample_rate=sample_rate
                    )
                else:
                    return dropped_result(
                        parent_span_context, attributes, sample_rate=sample_rate
                    )
            else:
                logger.debug(
                    f"[Tracing] Ignoring sampled param for non-root span {name}"
                )

        # Check if there is a traces_sampler
        # Traces_sampler is responsible to check parent sampled to have full transactions.
        has_traces_sampler = callable(client.options.get("traces_sampler"))

        sample_rate_to_propagate = None

        if is_root_span and has_traces_sampler:
            sampling_context = create_sampling_context(
                name, attributes, parent_span_context, trace_id
            )
            sample_rate = client.options["traces_sampler"](sampling_context)
            sample_rate_to_propagate = sample_rate
        else:
            # Check if there is a parent with a sampling decision
            parent_sampled = get_parent_sampled(parent_span_context, trace_id)
            parent_sample_rate = get_parent_sample_rate(parent_span_context, trace_id)
            if parent_sampled is not None:
                sample_rate = bool(parent_sampled)
                sample_rate_to_propagate = (
                    parent_sample_rate if parent_sample_rate else sample_rate
                )
            else:
                # Check if there is a traces_sample_rate
                sample_rate = client.options.get("traces_sample_rate")
                sample_rate_to_propagate = sample_rate

        # If the sample rate is invalid, drop the span
        if not is_valid_sample_rate(sample_rate, source=self.__class__.__name__):
            logger.warning(
                f"[Tracing] Discarding {name} because of invalid sample rate."
            )
            return dropped_result(parent_span_context, attributes)

        # Down-sample in case of back pressure monitor says so
        if is_root_span and client.monitor:
            sample_rate /= 2**client.monitor.downsample_factor
            if client.monitor.downsample_factor > 0:
                sample_rate_to_propagate = sample_rate

        # Roll the dice on sample rate
        sample_rate = float(cast("Union[bool, float, int]", sample_rate))
        sampled = random.random() < sample_rate

        if sampled:
            return sampled_result(
                parent_span_context, attributes, sample_rate=sample_rate_to_propagate
            )
        else:
            return dropped_result(
                parent_span_context, attributes, sample_rate=sample_rate_to_propagate
            )

    def get_description(self) -> str:
        return self.__class__.__name__


def create_sampling_context(name, attributes, parent_span_context, trace_id):
    # type: (str, Attributes, Optional[SpanContext], int) -> dict[str, Any]
    sampling_context = {
        "transaction_context": {
            "name": name,
            "op": attributes.get(SentrySpanAttribute.OP) if attributes else None,
            "source": (
                attributes.get(SentrySpanAttribute.SOURCE) if attributes else None
            ),
        },
        "parent_sampled": get_parent_sampled(parent_span_context, trace_id),
    }  # type: dict[str, Any]

    if attributes is not None:
        sampling_context.update(attributes)

    return sampling_context
