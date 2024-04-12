from sentry_sdk.tracing_utils import PropagationContext


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


def test_lacy_uuids():
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
