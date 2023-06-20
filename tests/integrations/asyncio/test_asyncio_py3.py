import asyncio
import sys

import pytest

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.asyncio import AsyncioIntegration


minimum_python_37 = pytest.mark.skipif(
    sys.version_info < (3, 7), reason="Asyncio tests need Python >= 3.7"
)


async def foo():
    await asyncio.sleep(0.01)


async def bar():
    await asyncio.sleep(0.01)


async def boom():
    1 / 0


@pytest.fixture(scope="session")
def event_loop(request):
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@minimum_python_37
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

    assert transaction_event["spans"][1]["op"] == OP.FUNCTION
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == OP.FUNCTION
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )


@minimum_python_37
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

    assert transaction_event["spans"][1]["op"] == OP.FUNCTION
    assert transaction_event["spans"][1]["description"] == "foo"
    assert (
        transaction_event["spans"][1]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )

    assert transaction_event["spans"][2]["op"] == OP.FUNCTION
    assert transaction_event["spans"][2]["description"] == "bar"
    assert (
        transaction_event["spans"][2]["parent_span_id"]
        == transaction_event["spans"][0]["span_id"]
    )


@minimum_python_37
@pytest.mark.asyncio
async def test_exception(
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

    with sentry_sdk.start_transaction(name="test_exception"):
        with sentry_sdk.start_span(op="root", description="not so important"):
            tasks = [event_loop.create_task(boom()), event_loop.create_task(bar())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

            sentry_sdk.flush()

    (error_event, _) = events

    assert error_event["transaction"] == "test_exception"
    assert error_event["contexts"]["trace"]["op"] == "function"
    assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert error_event["exception"]["values"][0]["value"] == "division by zero"
    assert error_event["exception"]["values"][0]["mechanism"]["handled"] is False
    assert error_event["exception"]["values"][0]["mechanism"]["type"] == "asyncio"


@minimum_python_37
@pytest.mark.asyncio
async def test_task_result(sentry_init):
    sentry_init(
        integrations=[
            AsyncioIntegration(),
        ],
    )

    async def add(a, b):
        return a + b

    result = await asyncio.create_task(add(1, 2))
    assert result == 3, result
