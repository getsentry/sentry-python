from random import random

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


def dropped(parent_context=None):
    # type: (Optional[SpanContext]) -> SamplingResult
    trace_state = parent_context.trace_state if parent_context is not None else None
    updated_trace_context = trace_state or TraceState()
    updated_trace_context = updated_trace_context.update(
        SENTRY_TRACE_STATE_DROPPED, "true"
    )
    return SamplingResult(
        Decision.DROP,
        trace_state=updated_trace_context,
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

        parent_span = trace.get_current_span(parent_context)
        parent_context = parent_span.get_span_context() if parent_span else None

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return dropped(parent_context)

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
                "parent_sampled": get_parent_sampled(parent_context, trace_id),
            }

            sample_rate = client.options["traces_sampler"](sampling_context)

        else:
            # Check if there is a parent with a sampling decision
            parent_sampled = get_parent_sampled(parent_context, trace_id)
            if parent_sampled is not None:
                sample_rate = parent_sampled
            else:
                # Check if there is a traces_sample_rate
                sample_rate = client.options.get("traces_sample_rate")

        # If the sample rate is invalid, drop the span
        if not is_valid_sample_rate(sample_rate, source=self.__class__.__name__):
            logger.warning(
                f"[Tracing] Discarding {name} because of invalid sample rate."
            )
            return dropped(parent_context)

        # Roll the dice on sample rate
        sampled = random() < float(sample_rate)

        if sampled:
            return SamplingResult(Decision.RECORD_AND_SAMPLE)
        else:
            return dropped(parent_context)

    def get_description(self) -> str:
        return self.__class__.__name__
