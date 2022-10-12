import asyncio
import sys

import pytest
import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration


minimum_python_36 = pytest.mark.skipif(
    sys.version_info < (3, 6), reason="ASGI is only supported in Python >= 3.6"
)


async def foo():
    await asyncio.sleep(0.01)


async def bar():
    await asyncio.sleep(0.01)


@minimum_python_36
@pytest.mark.asyncio
async def test_create_task(
    sentry_init,
    capture_events,
    event_loop,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        debug=True,
        integrations=[
            AsyncioIntegration(),
        ],
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction_for_create_task"):
        with sentry_sdk.start_span(op="root", description="not so important"):
            tasks = [event_loop.create_task(foo()), event_loop.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

            sentry_sdk.flush()

    (transaction_event,) = events

    assert transaction_event["spans"][0]["op"] == "root"
    assert transaction_event["spans"][0]["description"] == "not so important"

    assert transaction_event["spans"][1]["op"] == "asynctask"
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == "asynctask"
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )


@minimum_python_36
@pytest.mark.asyncio
async def test_gather(
    sentry_init,
    capture_events,
):
    sentry_init(
        traces_sample_rate=1.0,
        send_default_pii=True,
        debug=True,
        integrations=[
            AsyncioIntegration(),
        ],
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction_for_gather"):
        with sentry_sdk.start_span(op="root", description="not so important"):
            await asyncio.gather(foo(), bar(), return_exceptions=True)

        sentry_sdk.flush()

    (transaction_event,) = events

    assert transaction_event["spans"][0]["op"] == "root"
    assert transaction_event["spans"][0]["description"] == "not so important"

    assert transaction_event["spans"][1]["op"] == "asynctask"
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == "asynctask"
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )
