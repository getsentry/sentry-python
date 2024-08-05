from uuid import UUID

import dramatiq
import sentry_sdk


def test_that_a_single_error_is_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_that_actor_name_is_set_as_transaction(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["transaction"] == "dummy_actor"


def test_that_dramatiq_message_id_is_set_as_tag(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sentry_sdk.capture_message("hi")
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2 = events
    assert "dramatiq_message_id" in event1["tags"]
    msg_ids = [e["tags"]["dramatiq_message_id"] for e in events]
    assert all(UUID(msg_id) and isinstance(msg_id, str) for msg_id in msg_ids)


def test_that_local_variables_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        foo = 42  # noqa
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
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


def test_that_sub_actor_errors_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        sub_actor.send(x, y)

    @dramatiq.actor(max_retries=0)
    def sub_actor(x, y):
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events
    assert event["transaction"] == "sub_actor"

    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"


def test_that_multiple_errors_are_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    dummy_actor.send(1, None)
    broker.join(dummy_actor.queue_name)
    worker.join()

    event1, event2 = events

    assert event1["transaction"] == "dummy_actor"
    exception = event1["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"

    assert event2["transaction"] == "dummy_actor"
    exception = event2["exception"]["values"][0]
    assert exception["type"] == "TypeError"


def test_that_message_data_is_added_as_request(broker, worker, capture_events):
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
    broker.join(dummy_actor.queue_name)
    worker.join()

    (event,) = events

    assert event["transaction"] == "dummy_actor"
    request_data = event["contexts"]["dramatiq"]["data"]
    assert request_data["queue_name"] == "default"
    assert request_data["actor_name"] == "dummy_actor"
    assert request_data["args"] == [1, 0]
    assert request_data["kwargs"] == {}
    assert request_data["options"]["max_retries"] == 0
    assert UUID(request_data["message_id"])
    assert isinstance(request_data["message_timestamp"], int)


def test_that_expected_exceptions_are_not_captured(broker, worker, capture_events):
    events = capture_events()

    class ExpectedException(Exception):
        pass

    @dramatiq.actor(max_retries=0, throws=ExpectedException)
    def dummy_actor():
        raise ExpectedException

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    assert events == []


def test_that_retry_exceptions_are_not_captured(broker, worker, capture_events):
    events = capture_events()

    @dramatiq.actor(max_retries=2)
    def dummy_actor():
        raise dramatiq.errors.Retry("Retrying", delay=100)

    dummy_actor.send()
    broker.join(dummy_actor.queue_name)
    worker.join()

    assert events == []
