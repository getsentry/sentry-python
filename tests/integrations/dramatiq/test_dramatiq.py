import uuid

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import Middleware, SkipMessage

import sentry_sdk
from sentry_sdk.consts import SPANDATA, SPANSTATUS
from sentry_sdk.integrations.dramatiq import DramatiqIntegration
from sentry_sdk.integrations.logging import ignore_logger_for_events

ignore_logger_for_events("dramatiq.worker.WorkerThread")


@pytest.fixture(scope="function")
def broker(request, sentry_init):
    param = getattr(request, "param", None)
    if isinstance(param, dict):
        sentry_init(integrations=[DramatiqIntegration()], **param)
    else:
        sentry_init(
            integrations=[DramatiqIntegration()],
            traces_sample_rate=param,
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
        (
            {
                "traces_sample_rate": 1.0,
                "trace_lifecycle": "stream",
            },
            SPANSTATUS.INTERNAL_ERROR,
            False,
        ),
        (
            {
                "traces_sample_rate": 1.0,
                "trace_lifecycle": "stream",
            },
            SPANSTATUS.OK,
            False,
        ),
        (
            {
                "traces_sample_rate": 1.0,
                "trace_lifecycle": "stream",
            },
            SPANSTATUS.INTERNAL_ERROR,
            True,
        ),
        (
            {
                "traces_sample_rate": 1.0,
                "trace_lifecycle": "stream",
            },
            SPANSTATUS.OK,
            True,
        ),
    ],
    ids=[
        "error",
        "success",
        "error_fail_fast",
        "success_fail_fast",
    ],
    indirect=["broker"],
)
def test_task_transaction(
    broker,
    worker,
    capture_events,
    capture_items,
    expected_span_status,
    fail_fast,
):
    task_fails = expected_span_status == SPANSTATUS.INTERNAL_ERROR

    items = capture_items("event", "span")

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
    sentry_sdk.flush()

    if task_fails:
        error_item, segment_item = items
        error_event = error_item.payload
        exception = error_event["exception"]["values"][0]
        assert exception["type"] == "ZeroDivisionError"
        assert exception["mechanism"]["type"] == DramatiqIntegration.identifier
    else:
        (segment_item,) = items

    segment = segment_item.payload
    assert segment_item.type == "span"
    assert segment["name"] == "dummy_actor"
    assert segment["is_segment"] is True
    assert segment["attributes"]["sentry.op"] == "queue.task.dramatiq"
    assert segment["attributes"]["sentry.segment.name.source"] == "task"
    assert (
        segment["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME]
        == dummy_actor.queue_name
    )
    assert segment["status"] == ("error" if task_fails else "ok")


@pytest.mark.parametrize(
    "broker",
    [
        {
            "traces_sample_rate": 1.0,
            "trace_lifecycle": "stream",
        },
    ],
    indirect=["broker"],
)
def test_dramatiq_propagate_trace(broker, worker, capture_items):
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="outer") as outer_span:

        @dramatiq.actor(max_retries=0)
        def propagated_trace_task():
            pass

        propagated_trace_task.send()
        broker.join(propagated_trace_task.queue_name)
        worker.join()

    sentry_sdk.flush()

    inner_segment, outer_segment = [i.payload for i in items]
    assert inner_segment["name"] == "propagated_trace_task"
    assert inner_segment["attributes"]["sentry.op"] == "queue.task.dramatiq"
    assert inner_segment["trace_id"] == outer_span.trace_id
    assert outer_segment["name"] == "outer"


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
    "broker,expect_message_data",
    [
        pytest.param({}, True, id="data_collection_not_enabled"),
        pytest.param(
            {
                "_experiments": {
                    "data_collection": {"http_bodies": ["incoming_request"]}
                }
            },
            True,
            id="data_collection_http_bodies_incoming_request",
        ),
        pytest.param(
            {"_experiments": {"data_collection": {"http_bodies": []}}},
            False,
            id="data_collection_http_bodies_empty",
        ),
    ],
    indirect=["broker"],
)
def test_that_message_data_is_gated_by_data_collection(
    broker, worker, capture_events, expect_message_data
):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send_with_options(args=(1, 0), max_retries=0)
    broker.join(dummy_actor.queue_name, fail_fast=False)
    worker.join()

    (event,) = events
    dramatiq_context = event["contexts"]["dramatiq"]

    if expect_message_data:
        assert dramatiq_context["data"]["actor_name"] == "dummy_actor"
        assert dramatiq_context["data"]["args"] == [1, 0]
    else:
        assert "data" not in dramatiq_context


@pytest.mark.parametrize(
    "broker",
    [{"_experiments": {"data_collection": {"http_bodies": []}}}],
    indirect=True,
)
def test_that_dramatiq_context_type_is_set_regardless_of_data_collection(
    broker, worker, capture_events
):
    events = capture_events()

    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        return x / y

    dummy_actor.send_with_options(args=(1, 0), max_retries=0)
    broker.join(dummy_actor.queue_name, fail_fast=False)
    worker.join()

    (event,) = events
    dramatiq_context = event["contexts"]["dramatiq"]

    assert dramatiq_context["type"] == "dramatiq"
    assert "data" not in dramatiq_context


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


@pytest.mark.parametrize(
    "broker",
    [
        {
            "traces_sample_rate": 1.0,
            "trace_lifecycle": "stream",
        },
    ],
    indirect=["broker"],
)
def test_that_skip_message_cleans_up_scope_and_transaction(
    broker, worker, capture_items
):
    captured_spans: list = []

    class SkipMessageMiddleware(Middleware):
        def before_process_message(self, broker, message):
            captured_spans.append(sentry_sdk.get_current_span())
            raise SkipMessage()

    broker.add_middleware(SkipMessageMiddleware())

    items = capture_items("span")

    @dramatiq.actor(max_retries=0)
    def skipped_actor(): ...

    skipped_actor.send()

    broker.join(skipped_actor.queue_name)
    worker.join()

    sentry_sdk.flush()
    (segment_payload,) = [i.payload for i in items]
    assert segment_payload["name"] == "skipped_actor"
    assert segment_payload["end_timestamp"] is not None
