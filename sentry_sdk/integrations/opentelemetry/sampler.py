import random

from opentelemetry import trace

from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace.span import TraceState

import sentry_sdk
from sentry_sdk.integrations.opentelemetry.consts import SENTRY_TRACE_STATE_DROPPED
from sentry_sdk.tracing_utils import has_tracing_enabled
from sentry_sdk.utils import is_valid_sample_rate, logger

from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
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
        if parent_context.trace_flags.sampled:
            return True

        dropped = parent_context.trace_state.get(SENTRY_TRACE_STATE_DROPPED) == "true"
        if dropped:
            return False

        # TODO-anton: fall back to sampling decision in DSC (for this die DSC needs to be set in the trace_state)

    return None


def dropped_result(span_context):
    # type: (SpanContext) -> SamplingResult
    trace_state = span_context.trace_state.update(SENTRY_TRACE_STATE_DROPPED, "true")

    # Tell Sentry why we dropped the transaction/span
    client = sentry_sdk.get_client()
    if client.monitor and client.monitor.downsample_factor > 0:
        reason = "backpressure"
    else:
        reason = "sample_rate"

    client.transport.record_lost_event(reason, data_category="transaction")

    # Only one span (the transaction itself) is discarded, since we did not record any spans here.
    client.transport.record_lost_event(reason, data_category="span")

    return SamplingResult(
        Decision.DROP,
        trace_state=trace_state,
    )


def sampled_result(span_context):
    # type: (SpanContext) -> SamplingResult
    return SamplingResult(
        Decision.RECORD_AND_SAMPLE,
        trace_state=span_context.trace_state,
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

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return dropped_result(parent_span_context)

        sample_rate = None

        # Check if sampled=True was passed to start_transaction
        # TODO-anton: Do we want to keep the start_transaction(sampled=True) thing?

        # Check if there is a traces_sampler
        # Traces_sampler is responsible to check parent sampled to have full transactions.
        has_traces_sampler = callable(client.options.get("traces_sampler"))
        if has_traces_sampler:
            # TODO-anton: Make proper sampling_context
            sampling_context = {
                "transaction_context": {
                    "name": name,
                },
                "parent_sampled": get_parent_sampled(parent_span_context, trace_id),
            }

            sample_rate = client.options["traces_sampler"](sampling_context)

        else:
            # Check if there is a parent with a sampling decision
            parent_sampled = get_parent_sampled(parent_span_context, trace_id)
            if parent_sampled is not None:
                sample_rate = parent_sampled
            else:
                # Check if there is a traces_sample_rate
                sample_rate = client.options.get("traces_sample_rate")

        # Down-sample in case of back pressure monitor says so
        # TODO: this should only be done for transactions (aka root spans)
        if client.monitor:
            sample_rate /= 2**client.monitor.downsample_factor

        # If the sample rate is invalid, drop the span
        if not is_valid_sample_rate(sample_rate, source=self.__class__.__name__):
            logger.warning(
                f"[Tracing] Discarding {name} because of invalid sample rate."
            )
            return dropped_result(parent_span_context)

        # Roll the dice on sample rate
        sampled = random.random() < float(sample_rate)

        # TODO-neel-potel set sample rate as attribute for DSC
        if sampled:
            return sampled_result(parent_span_context)
        else:
            return dropped_result(parent_span_context)

    def get_description(self) -> str:
        return self.__class__.__name__
