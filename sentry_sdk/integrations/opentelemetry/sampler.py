from random import random

from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace import SpanKind
from opentelemetry.util.types import Attributes

import sentry_sdk
from sentry_sdk.tracing_utils import has_tracing_enabled

from typing import Optional, Sequence
    

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
        """
            Decisions:
            # IsRecording() == false, span will not be recorded and all events and attributes will be dropped.
            DROP = 0
            # IsRecording() == true, but Sampled flag MUST NOT be set.
            RECORD_ONLY = 1
            # IsRecording() == true AND Sampled flag` MUST be set.
            RECORD_AND_SAMPLE = 2

            Recorded:
                sent to sentry
            Sampled:
                it is not dropped, but could be NonRecordingSpan that is never sent to Sentry (for trace propagation)
        """
        """
        SamplingResult
            decision: A sampling decision based off of whether the span is recorded
                and the sampled flag in trace flags in the span context.
            attributes: Attributes to add to the `opentelemetry.trace.Span`.
            trace_state: The tracestate used for the `opentelemetry.trace.Span`.
                Could possibly have been modified by the sampler.
        """
        """
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
        client = sentry_sdk.get_client()

        # No tracing enabled, thus no sampling
        if not has_tracing_enabled(client.options):
            return SamplingResult(Decision.DROP)

        # Check if sampled=True was passed to start_transaction
        #TODO-anton: Do we want to keep the start_transaction(sampled=True) thing?        

        parent_span = trace.get_current_span()
        parent_context = parent_span.get_span_context()

        # Check if there is a traces_sampler
        import ipdb; ipdb.set_trace()
        has_traces_sampler = callable(client.options.get("traces_sampler"))
        if has_traces_sampler:
            #TODO-anton: Make proper sampling_context
            sampling_context = {
                "transaction_context": {
                    "name": name,
                },
                "parent_sampled": parent_context.trace_flags.sampled,
            }

            # TODO-anton: use is_valid_sample_rate()
            sample_rate = client.options["traces_sampler"](sampling_context)

        # Check if there is a parent with a sampling decision
        has_parent = parent_context is not None and parent_context.is_valid
        if has_parent:
            if parent_context.trace_flags.sampled:
                return SamplingResult(Decision.RECORD_AND_SAMPLE)
            else:
                return SamplingResult(Decision.DROP)


        # Roll the dice on traces_sample_rate
        sample_rate = client.options.get("traces_sample_rate")
        if sample_rate is not None:
            sampled = random() < sample_rate
            if sampled:
                return SamplingResult(Decision.RECORD_AND_SAMPLE)
            else:
                return SamplingResult(Decision.DROP)

        if sampled:
            return SamplingResult(Decision.RECORD_AND_SAMPLE)
        else:
            return SamplingResult(Decision.DROP)

    def get_description(self) -> str:
        print("YYYYYYYYYYYYYYYY")
        return "SentrySampler"
