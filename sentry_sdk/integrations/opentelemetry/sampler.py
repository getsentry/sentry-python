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


def dropped_result(parent_span_context, attributes, sample_rate=None):
    # type: (SpanContext, Attributes, Optional[float]) -> SamplingResult
    # these will only be added the first time in a root span sampling decision
    trace_state = parent_span_context.trace_state

    if TRACESTATE_SAMPLED_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLED_KEY, "false")

    if sample_rate and TRACESTATE_SAMPLE_RATE_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate))

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
    # type: (SpanContext, Attributes, float) -> SamplingResult
    # these will only be added the first time in a root span sampling decision
    trace_state = span_context.trace_state

    if TRACESTATE_SAMPLED_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLED_KEY, "true")
    if TRACESTATE_SAMPLE_RATE_KEY not in trace_state:
        trace_state = trace_state.add(TRACESTATE_SAMPLE_RATE_KEY, str(sample_rate))

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

        # Explicit sampled value provided at start_span
        if attributes.get(SentrySpanAttribute.CUSTOM_SAMPLED) is not None:
            if is_root_span:
                sample_rate = float(attributes[SentrySpanAttribute.CUSTOM_SAMPLED])
                if sample_rate > 0:
                    return sampled_result(parent_span_context, attributes, sample_rate)
                else:
                    return dropped_result(parent_span_context, attributes)
            else:
                logger.debug(
                    f"[Tracing] Ignoring sampled param for non-root span {name}"
                )

        sample_rate = None

        # Check if there is a traces_sampler
        # Traces_sampler is responsible to check parent sampled to have full transactions.
        has_traces_sampler = callable(client.options.get("traces_sampler"))
        custom_parent_sampled = attributes.get(
            SentrySpanAttribute.CUSTOM_PARENT_SAMPLED
        )
        if custom_parent_sampled is not None:
            parent_sampled = custom_parent_sampled
        else:
            parent_sampled = get_parent_sampled(parent_span_context, trace_id)
        print("custom_parent_sampled", custom_parent_sampled)
        print("get parent sampled", get_parent_sampled(parent_span_context, trace_id))

        if is_root_span and has_traces_sampler:
            sampling_context = {
                "transaction_context": {
                    "name": name,
                    "op": attributes.get(SentrySpanAttribute.OP),
                },
                "parent_sampled": parent_sampled,
            }
            sampling_context.update(attributes)
            sample_rate = client.options["traces_sampler"](sampling_context)

        else:
            # Check if there is a parent with a sampling decision
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

        # Down-sample in case of back pressure monitor says so
        if is_root_span and client.monitor:
            sample_rate /= 2**client.monitor.downsample_factor

        # Roll the dice on sample rate
        sample_rate = float(cast("Union[bool, float, int]", sample_rate))
        sampled = random.random() < sample_rate

        if sampled:
            return sampled_result(parent_span_context, attributes, sample_rate)
        else:
            return dropped_result(parent_span_context, attributes, sample_rate)

    def get_description(self) -> str:
        return self.__class__.__name__
