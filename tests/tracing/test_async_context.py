import asyncio
import sys


import pytest
import sentry_sdk


async def concurrent_task(task_id):
    with sentry_sdk.start_span(op="task", name=f"concurrent_task_{task_id}") as span:
        await asyncio.sleep(0.01)
        return span


async def parent_task():
    with sentry_sdk.start_span(op="task", name="parent_task") as parent_span:
        futures = [concurrent_task(i) for i in range(3)]
        spans = await asyncio.gather(*futures)
        return parent_span, spans


@pytest.mark.skipif(
    sys.version_info < (3, 7),
    reason="python 3.6 lacks proper contextvar support. Contextvars are needed to prevent scope bleed in async context",
)
@pytest.mark.asyncio
async def test_concurrent_spans_are_children():
    sentry_sdk.init(
        debug=True,
        traces_sample_rate=1.0,
    )

    with sentry_sdk.start_transaction(
        op="test", name="test_transaction"
    ) as transaction:
        parent_span, child_spans = await parent_task()

        # Verify parent span is a child of the transaction
        assert parent_span.parent_span_id == transaction.span_id
        # Verify all child spans are children of the parent span
        for span in child_spans:
            print(span.description)
            assert span.parent_span_id == parent_span.span_id
            assert span.op == "task"
            assert span.description.startswith("concurrent_task_")

        # Verify we have the expected number of child spans
        assert len(child_spans) == 3
