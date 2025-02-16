import pytest

from sentry_sdk.tracing_utils import PropagationContext


SAMPLED_FLAG = {
    None: "",
    False: "-0",
    True: "-1",
}
"""Maps the `sampled` value to the flag appended to the sentry-trace header."""


def test_empty_context():
    ctx = PropagationContext()

    assert ctx.trace_id is not None
    assert len(ctx.trace_id) == 32

    assert ctx.span_id is not None
    assert len(ctx.span_id) == 16

    assert ctx.parent_span_id is None
    assert ctx.parent_sampled is None
    assert ctx.dynamic_sampling_context is None


def test_context_with_values():
    ctx = PropagationContext(
        trace_id="1234567890abcdef1234567890abcdef",
        span_id="1234567890abcdef",
        parent_span_id="abcdef1234567890",
        parent_sampled=True,
        dynamic_sampling_context={
            "foo": "bar",
        },
    )

    assert ctx.trace_id == "1234567890abcdef1234567890abcdef"
    assert ctx.span_id == "1234567890abcdef"
    assert ctx.parent_span_id == "abcdef1234567890"
    assert ctx.parent_sampled
    assert ctx.dynamic_sampling_context == {
        "foo": "bar",
    }


def test_lazy_uuids():
    ctx = PropagationContext()
    assert ctx._trace_id is None
    assert ctx._span_id is None

    assert ctx.trace_id is not None  # this sets _trace_id
    assert ctx._trace_id is not None
    assert ctx._span_id is None

    assert ctx.span_id is not None  # this sets _span_id
    assert ctx._trace_id is not None
    assert ctx._span_id is not None


def test_property_setters():
    ctx = PropagationContext()

    ctx.trace_id = "X234567890abcdef1234567890abcdef"
    ctx.span_id = "X234567890abcdef"

    assert ctx._trace_id == "X234567890abcdef1234567890abcdef"
    assert ctx.trace_id == "X234567890abcdef1234567890abcdef"
    assert ctx._span_id == "X234567890abcdef"
    assert ctx.span_id == "X234567890abcdef"
    assert ctx.dynamic_sampling_context is None


def test_update():
    ctx = PropagationContext()

    other_data = {
        "trace_id": "Z234567890abcdef1234567890abcdef",
        "parent_span_id": "Z234567890abcdef",
        "parent_sampled": False,
        "foo": "bar",
    }
    ctx.update(other_data)

    assert ctx._trace_id == "Z234567890abcdef1234567890abcdef"
    assert ctx.trace_id == "Z234567890abcdef1234567890abcdef"
    assert ctx._span_id is None  # this will be set lazily
    assert ctx.span_id is not None  # this sets _span_id
    assert ctx._span_id is not None
    assert ctx.parent_span_id == "Z234567890abcdef"
    assert not ctx.parent_sampled
    assert ctx.dynamic_sampling_context is None

    assert not hasattr(ctx, "foo")


def test_existing_sample_rand_kept():
    ctx = PropagationContext(
        trace_id="00000000000000000000000000000000",
        dynamic_sampling_context={"sample_rand": "0.5"},
    )

    # If sample_rand was regenerated, the value would be 0.8766381713144122 based on the trace_id
    assert ctx.dynamic_sampling_context["sample_rand"] == "0.5"


@pytest.mark.parametrize(
    ("parent_sampled", "sample_rate", "expected_sample_rand"),
    (
        (None, None, "0.8766381713144122"),
        (None, "0.5", "0.8766381713144122"),
        (False, None, "0.8766381713144122"),
        (True, None, "0.8766381713144122"),
        (False, "0.0", "0.8766381713144122"),
        (False, "0.01", "0.8778717896012681"),
        (True, "0.01", "0.008766381713144122"),
        (False, "0.1", "0.888974354182971"),
        (True, "0.1", "0.08766381713144122"),
        (False, "0.5", "0.9383190856572061"),
        (True, "0.5", "0.4383190856572061"),
        (True, "1.0", "0.8766381713144122"),
    ),
)
def test_sample_rand_filled(parent_sampled, sample_rate, expected_sample_rand):
    """When continuing a trace, we want to fill in the sample_rand value if it's missing."""
    dsc = {}
    if sample_rate is not None:
        dsc["sample_rate"] = sample_rate

    ctx = PropagationContext().from_incoming_data(
        {
            "sentry-trace": f"00000000000000000000000000000000-0000000000000000{SAMPLED_FLAG[parent_sampled]}",
            "baggage": f"sentry-sample_rate={sample_rate}",
        }
    )

    assert ctx.dynamic_sampling_context["sample_rand"] == expected_sample_rand
