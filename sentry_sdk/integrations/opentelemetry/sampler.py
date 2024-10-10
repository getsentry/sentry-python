from typing import cast
from random import random

from opentelemetry import trace

from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace.span import TraceState

import sentry_sdk
from sentry_sdk.tracing_utils import has_tracing_enabled
from sentry_sdk.utils import is_valid_sample_rate, logger
from sentry_sdk.integrations.opentelemetry.consts import (
    TRACESTATE_SAMPLED_KEY,
    TRACESTATE_SAMPLE_RATE_KEY,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional, Sequence, Union
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

        dsc_sampled = parent_context.trace_state.get(TRACESTATE_SAMPLED_KEY)
        if dsc_sampled == "true":
            return True
        elif dsc_sampled == "false":
            return False

    return None


def dropped_result(span_context, attributes, sample_rate=None):
    # type: (SpanContext, Attributes, Optional[float]) -> SamplingResult
    # note that trace_state.add will NOT overwrite existing entries
    # so these will only be added the first time in a root span sampling decision
    trace_state = span_context.trace_state.add(TRACESTATE_SAMPLED_KEY, "false")
    if sample_rate:
        trace_state = trace_state.add(TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate))

    return SamplingResult(
        Decision.DROP,
        attributes=attributes,
        trace_state=trace_state,
    )


def sampled_result(span_context, attributes, sample_rate):
    # type: (SpanContext, Attributes, float) -> SamplingResult
    # note that trace_state.add will NOT overwrite existing entries
    # so these will only be added the first time in a root span sampling decision
    trace_state = span_context.trace_state.add(TRACESTATE_SAMPLED_KEY, "true").add(
        TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate)
    )

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

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return dropped_result(parent_span_context, attributes)

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

        # If the sample rate is invalid, drop the span
        if not is_valid_sample_rate(sample_rate, source=self.__class__.__name__):
            logger.warning(
                f"[Tracing] Discarding {name} because of invalid sample rate."
            )
            return dropped_result(parent_span_context, attributes)

        # Roll the dice on sample rate
        sample_rate = float(cast("Union[bool, float, int]", sample_rate))
        sampled = random() < sample_rate

        if sampled:
            return sampled_result(parent_span_context, attributes, sample_rate)
        else:
            return dropped_result(parent_span_context, attributes, sample_rate)

    def get_description(self) -> str:
        return self.__class__.__name__
