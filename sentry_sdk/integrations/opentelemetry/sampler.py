from random import random

from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace import SpanKind
from opentelemetry.trace.span import TraceState
from opentelemetry.util.types import Attributes

import sentry_sdk
from sentry_sdk.integrations.opentelemetry.consts import SENTRY_TRACE_STATE_DROPPED
from sentry_sdk.tracing_utils import has_tracing_enabled
from sentry_sdk.utils import is_valid_sample_rate, logger

from typing import Optional, Sequence


def get_parent_sampled(parent_context, trace_id):
    # type: (Optional["Context"], int) -> Optional[bool]
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


class SentrySampler(Sampler):
    def should_sample(
        self,
        parent_context: Optional["Context"],
        trace_id: int,
        name: str,
        kind: Optional[SpanKind] = None,
        attributes: Attributes = None,
        links: Optional[Sequence["Link"]] = None,
        trace_state: Optional["TraceState"] = None,
    ) -> "SamplingResult":
        client = sentry_sdk.get_client()

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return SamplingResult(Decision.DROP)

        # Check if sampled=True was passed to start_transaction
        # TODO-anton: Do we want to keep the start_transaction(sampled=True) thing?

        parent_span = trace.get_current_span(parent_context)
        parent_context = parent_span and parent_span.get_span_context()

        sample_rate = None
        # Check if there is a traces_sampler
        # Traces_sampler is responsible to check parent sampled to have full transactions.
        has_traces_sampler = callable(client.options.get("traces_sampler"))
        if has_traces_sampler:
            # TODO-anton: Make proper sampling_context
            sampling_context = {
                "transaction_context": {
                    "name": name,
                },
                "parent_sampled": parent_context.trace_flags.sampled,
            }

            sample_rate = client.options["traces_sampler"](sampling_context)

        # Check if there is a parent with a sampling decision
        if sample_rate is None:
            parent_sampled = get_parent_sampled(parent_context, trace_id)
            if parent_sampled is not None:
                sample_rate = parent_sampled

        # Check if there is a traces_sample_rate
        if sample_rate is None:
            sample_rate = client.options.get("traces_sample_rate")

        # If we still have no sample rate (but tracing is enabled,
        # because has_tracing_enabled returned True above), default to 1.0
        if sample_rate is None:
            sample_rate = 1.0

        # If the sample rate is invalid, drop the span
        if not is_valid_sample_rate(sample_rate, source=self.__class__.__name__):
            logger.warning(
                f"[Tracing] Discarding {name} because of invalid sample rate."
            )
            return SamplingResult(Decision.DROP)

        # Roll the dice on sample rate
        sampled = random() < float(sample_rate)

        if sampled:
            return SamplingResult(Decision.RECORD_AND_SAMPLE)
        else:
            return SamplingResult(
                Decision.DROP,
                trace_state=TraceState(
                    [
                        (SENTRY_TRACE_STATE_DROPPED, "true"),
                    ]
                ),
            )

    def get_description(self) -> str:
        return self.__class__.__name__
