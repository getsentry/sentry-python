import pytest
import uuid

import dramatiq
from dramatiq.brokers.stub import StubBroker

import sentry_sdk
from sentry_sdk.tracing import TransactionSource
from sentry_sdk import start_transaction
from sentry_sdk.consts import SPANSTATUS
from sentry_sdk.integrations.dramatiq import DramatiqIntegration
from sentry_sdk.integrations.logging import ignore_logger

ignore_logger("dramatiq.worker.WorkerThread")


@pytest.fixture(scope="function")
def broker(request, sentry_init):
    sentry_init(
        integrations=[DramatiqIntegration()],
        traces_sample_rate=getattr(request, "param", None),
    )
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def worker(broker):
    worker = dramatiq.Worker(broker, worker_timeout=100, worker_threads=1)
    worker.start()
    yield worker
    worker.stop()


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_a_single_error_is_captured(broker, worker, capture_events, fail_fast):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    (event,) = events
    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


@pytest.mark.parametrize(
    "broker,expected_span_status,fail_fast",
    [
        (1.0, SPANSTATUS.INTERNAL_ERROR, False),
        (1.0, SPANSTATUS.OK, False),
        (1.0, SPANSTATUS.INTERNAL_ERROR, True),
        (1.0, SPANSTATUS.OK, True),
    ],
    ids=["error", "success", "error_fail_fast", "success_fail_fast"],
    indirect=["broker"],
)
def test_task_transaction(
    broker, worker, capture_events, expected_span_status, fail_fast
):
    events = capture_events()
    task_fails = expected_span_status == SPANSTATUS.INTERNAL_ERROR

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, int(not task_fails))

    if expected_span_status == SPANSTATUS.INTERNAL_ERROR and fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)

    worker.join()

    if task_fails:
        error_event = events.pop(0)
        exception = error_event["exception"]["values"][0]
        assert exception["type"] == "ZeroDivisionError"
        assert exception["mechanism"]["type"] == DramatiqIntegration.identifier

    (event,) = events
    assert event["type"] == "transaction"
    assert event["transaction"] == "dummy_actor"
    assert event["transaction_info"] == {"source": TransactionSource.TASK}
    assert event["contexts"]["trace"]["status"] == expected_span_status


@pytest.mark.parametrize("broker", [1.0], indirect=True)
def test_dramatiq_propagate_trace(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def propagated_trace_task():
        pass

    with start_transaction() as outer_transaction:
        propagated_trace_task.send()
        broker.join(propagated_trace_task.queue_name)
        worker.join()

    assert (
        events[0]["transaction"] == "propagated_trace_task"
    )  # the "inner" transaction
    assert events[0]["contexts"]["trace"]["trace_id"] == outer_transaction.trace_id


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_dramatiq_message_id_is_set_as_extra(
    broker, worker, capture_events, fail_fast
):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sentry_sdk.capture_message("hi")
        return x / y

    dummy_actor.send(1, 0)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    event_message, event_error = events
    assert "dramatiq_message_id" in event_message["extra"]
    assert "dramatiq_message_id" in event_error["extra"]
    assert (
        event_message["extra"]["dramatiq_message_id"]
        == event_error["extra"]["dramatiq_message_id"]
    )
    msg_ids = [e["extra"]["dramatiq_message_id"] for e in events]
    assert all(uuid.UUID(msg_id) and isinstance(msg_id, str) for msg_id in msg_ids)


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_local_variables_are_captured(broker, worker, capture_events, fail_fast):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        foo = 42  # noqa
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    (event,) = events
    exception = event["exception"]["values"][0]
    assert exception["stacktrace"]["frames"][-1]["vars"] == {
        "x": "1",
        "y": "0",
        "foo": "42",
    }


def test_that_messages_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor():
        sentry_sdk.capture_message("hi")

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["message"] == "hi"
    assert event["level"] == "info"
    assert event["transaction"] == "dummy_actor"


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_sub_actor_errors_are_captured(broker, worker, capture_events, fail_fast):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sub_actor.send(x, y)

    @dramatiq.actor(max_retries=0)
    def sub_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    (event,) = events
    assert event["transaction"] == "sub_actor"

    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_multiple_errors_are_captured(broker, worker, capture_events, fail_fast):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 0)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    dummy_actor.send(1, None)
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    event1, event2 = events

    assert event1["transaction"] == "dummy_actor"
    exception = event1["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"

    assert event2["transaction"] == "dummy_actor"
    exception = event2["exception"]["values"][0]
    assert exception["type"] == "TypeError"


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_message_data_is_added_as_request(
    broker, worker, capture_events, fail_fast
):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send_with_options(
        args=(
            1,
            0,
        ),
        max_retries=0,
    )
    if fail_fast:
        with pytest.raises(ZeroDivisionError):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    (event,) = events

    assert event["transaction"] == "dummy_actor"
    request_data = event["contexts"]["dramatiq"]["data"]
    assert request_data["queue_name"] == "default"
    assert request_data["actor_name"] == "dummy_actor"
    assert request_data["args"] == [1, 0]
    assert request_data["kwargs"] == {}
    assert request_data["options"]["max_retries"] == 0
    assert uuid.UUID(request_data["message_id"])
    assert isinstance(request_data["message_timestamp"], int)


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_expected_exceptions_are_not_captured(
    broker, worker, capture_events, fail_fast
):
    events = capture_events()

    class ExpectedException(Exception):
        pass

    @dramatiq.actor(max_retries=0, throws=ExpectedException)
    def dummy_actor():
        raise ExpectedException

    dummy_actor.send()
    if fail_fast:
        with pytest.raises(ExpectedException):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    assert events == []


@pytest.mark.parametrize(
    "fail_fast",
    [
        False,
        True,
    ],
)
def test_that_retry_exceptions_are_not_captured(
    broker, worker, capture_events, fail_fast
):
    events = capture_events()

    @dramatiq.actor(max_retries=2)
    def dummy_actor():
        raise dramatiq.errors.Retry("Retrying", delay=100)

    dummy_actor.send()
    if fail_fast:
        with pytest.raises(dramatiq.errors.Retry):
            broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    else:
        broker.join(dummy_actor.queue_name, fail_fast=fail_fast)
    worker.join()

    assert events == []
