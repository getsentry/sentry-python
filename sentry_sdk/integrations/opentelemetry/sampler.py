from random import random

from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace import SpanKind
from opentelemetry.util.types import Attributes

import sentry_sdk
from sentry_sdk.tracing_utils import has_tracing_enabled
from sentry_sdk.utils import logger

from typing import Optional, Sequence

from sentry_sdk.utils import is_valid_sample_rate


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
        # TODO-anton: Do we want to keep the start_transaction(sampled=True) thing?

        parent_span = trace.get_current_span()
        parent_context = parent_span.get_span_context()

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
            has_parent = parent_context is not None and parent_context.is_valid
            if has_parent:
                sample_rate = parent_context.trace_flags.sampled

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
        print(f"- name: {name} / sample_rate: {sample_rate} / sampled: {sampled}")
        if sampled:
            return SamplingResult(Decision.RECORD_AND_SAMPLE)
        else:
            return SamplingResult(Decision.DROP)

    def get_description(self) -> str:
        print("YYYYYYYYYYYYYYYY")
        return self.__class__.__name__
