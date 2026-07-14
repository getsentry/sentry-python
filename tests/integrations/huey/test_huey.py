from decimal import DivisionByZero

import pytest
from huey import __version__ as HUEY_VERSION
from huey.api import MemoryHuey, Result
from huey.exceptions import CancelExecution, RetryTask

import sentry_sdk
from sentry_sdk import start_transaction
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.huey import HueyIntegration
from sentry_sdk.traces import SegmentSource, SpanStatus
from sentry_sdk.utils import parse_version

HUEY_VERSION = parse_version(HUEY_VERSION)

try:
    from huey.api import chord, group
except ImportError:
    chord = None
    group = None


@pytest.fixture
def init_huey(sentry_init):
    def inner(has_span_streaming=None):
        sentry_init(
            integrations=[HueyIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
            _experiments={"trace_lifecycle": "stream"} if has_span_streaming else {},
        )

        return MemoryHuey(name="sentry_sdk")

    return inner


@pytest.fixture(autouse=True)
def flush_huey_tasks(init_huey):
    huey = init_huey()
    huey.flush()


def execute_huey_task(huey, func, *args, **kwargs):
    exceptions = kwargs.pop("exceptions", None)
    result = func(*args, **kwargs)
    task = huey.dequeue()
    if exceptions is not None:
        try:
            huey.execute(task)
        except exceptions:
            pass
    else:
        huey.execute(task)
    return result


def test_task_result(init_huey):
    huey = init_huey()

    @huey.task()
    def increase(num):
        return num + 1

    result = increase(3)

    assert isinstance(result, Result)
    assert len(huey) == 1
    task = huey.dequeue()
    assert huey.execute(task) == 4
    assert result.get() == 4


@pytest.mark.parametrize("task_fails", [True, False], ids=["error", "success"])
@pytest.mark.parametrize(
    "has_span_streaming", [True, False], ids=["streaming", "no_streaming"]
)
def test_task_transaction_or_segment(
    capture_events, capture_items, init_huey, task_fails, has_span_streaming
):
    huey = init_huey(has_span_streaming=has_span_streaming)

    @huey.task()
    def division(a, b):
        return a / b

    if has_span_streaming:
        items = capture_items("span")
        execute_huey_task(
            huey, division, 1, int(not task_fails), exceptions=(DivisionByZero,)
        )
        sentry_sdk.get_client().flush()

        payloads = [i.payload for i in items]
        # The task is enqueued without a wrapping span, so in streaming mode no
        # producer (queue.submit.huey) span is created (see the early return in
        # patch_enqueue). Only the consumer segment is emitted.
        assert len(payloads) == 1
        (execute_span,) = payloads

        assert execute_span["is_segment"]
        assert execute_span["attributes"]["sentry.op"] == OP.QUEUE_TASK_HUEY
        assert (
            execute_span["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME] == huey.name
        )
        assert execute_span["name"] == "division"
        assert execute_span["status"] == (
            SpanStatus.ERROR if task_fails else SpanStatus.OK
        )
    else:
        events = capture_events()
        execute_huey_task(
            huey, division, 1, int(not task_fails), exceptions=(DivisionByZero,)
        )

        if task_fails:
            error_event = events.pop(0)
            assert error_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
            assert error_event["exception"]["values"][0]["mechanism"]["type"] == "huey"

        (event,) = events
        assert event["type"] == "transaction"
        assert event["transaction"] == "division"
        assert event["transaction_info"] == {"source": "task"}
        assert (
            event["contexts"]["trace"]["data"][SPANDATA.MESSAGING_DESTINATION_NAME]
            == huey.name
        )

        if task_fails:
            assert event["contexts"]["trace"]["status"] == "internal_error"
        else:
            assert event["contexts"]["trace"]["status"] == "ok"

        assert "huey_task_id" in event["tags"]
        assert "huey_task_retry" in event["tags"]


@pytest.mark.parametrize(
    "has_span_streaming", [True, False], ids=["streaming", "no_streaming"]
)
def test_task_retry(capture_events, capture_items, init_huey, has_span_streaming):
    huey = init_huey(has_span_streaming=has_span_streaming)
    context = {"retry": True}

    @huey.task()
    def retry_task(context):
        if context["retry"]:
            context["retry"] = False
            raise RetryTask()

    if has_span_streaming:
        items = capture_items("span")
        execute_huey_task(huey, retry_task, context)
        sentry_sdk.get_client().flush()

        payloads = [i.payload for i in items]
        # The initial enqueue happens without a wrapping span, so no producer
        # span is created for it. The re-enqueue triggered by RetryTask happens
        # inside the running consumer segment, so it does get a child span.
        assert len(payloads) == 2

        re_enqueue_span, execute_span = payloads

        assert re_enqueue_span["attributes"]["sentry.op"] == OP.QUEUE_SUBMIT_HUEY
        assert not re_enqueue_span["is_segment"]

        assert execute_span["attributes"]["sentry.op"] == OP.QUEUE_TASK_HUEY
        assert execute_span["is_segment"]
        assert execute_span["name"] == "retry_task"
        assert execute_span["status"] == SpanStatus.OK

        assert len(huey) == 1

        task = huey.dequeue()
        huey.execute(task)

        sentry_sdk.get_client().flush()

        all_payloads = [i.payload for i in items]

        assert len(all_payloads) == 3
        retry_span = all_payloads[2]

        assert retry_span["is_segment"]
        assert retry_span["name"] == "retry_task"
        assert retry_span["status"] == SpanStatus.OK
        assert len(huey) == 0
    else:
        events = capture_events()
        result = execute_huey_task(huey, retry_task, context)
        (event,) = events

        assert event["transaction"] == "retry_task"
        assert event["tags"]["huey_task_id"] == result.task.id
        assert len(huey) == 1

        task = huey.dequeue()
        huey.execute(task)
        (event, _) = events

        assert event["transaction"] == "retry_task"
        assert event["tags"]["huey_task_id"] == result.task.id
        assert len(huey) == 0


@pytest.mark.parametrize(
    "has_span_streaming", [True, False], ids=["streaming", "no_streaming"]
)
def test_task_cancel_does_not_override_status(
    capture_events, capture_items, init_huey, has_span_streaming
):
    huey = init_huey(has_span_streaming=has_span_streaming)

    @huey.task()
    def cancel_task():
        raise CancelExecution()

    if has_span_streaming:
        items = capture_items("span")
        execute_huey_task(huey, cancel_task)
        sentry_sdk.get_client().flush()

        payloads = [i.payload for i in items]
        # Enqueued without a wrapping span -> no producer span in streaming mode.
        assert len(payloads) == 1
        (execute_span,) = payloads

        assert execute_span["attributes"]["sentry.op"] == OP.QUEUE_TASK_HUEY
        assert execute_span["is_segment"]
        assert execute_span["name"] == "cancel_task"
        assert execute_span["status"] == SpanStatus.OK
    else:
        events = capture_events()
        execute_huey_task(huey, cancel_task)

        if HUEY_VERSION < (3, 0, 1):
            (event, _) = events
        else:
            (event,) = events
        assert event["transaction"] == "cancel_task"
        assert event["contexts"]["trace"]["status"] == "aborted"


@pytest.mark.parametrize("lock_name", ["lock.a", "lock.b"], ids=["locked", "unlocked"])
@pytest.mark.parametrize(
    "has_span_streaming", [True, False], ids=["streaming", "no_streaming"]
)
@pytest.mark.skipif(HUEY_VERSION < (2, 5), reason="is_locked was added in 2.5")
def test_task_lock(
    capture_events, capture_items, init_huey, lock_name, has_span_streaming
):
    huey = init_huey(has_span_streaming=has_span_streaming)

    task_lock_name = "lock.a"
    should_be_locked = task_lock_name == lock_name

    @huey.task()
    @huey.lock_task(task_lock_name)
    def maybe_locked_task():
        pass

    if has_span_streaming:
        items = capture_items("span")
        with huey.lock_task(lock_name):
            assert huey.is_locked(task_lock_name) == should_be_locked
            execute_huey_task(huey, maybe_locked_task)
        sentry_sdk.get_client().flush()

        payloads = [i.payload for i in items]
        # Enqueued without a wrapping span -> no producer span in streaming mode.
        assert len(payloads) == 1
        (execute_span,) = payloads

        assert execute_span["attributes"]["sentry.op"] == OP.QUEUE_TASK_HUEY

        assert execute_span["is_segment"]
        assert execute_span["name"] == "maybe_locked_task"
        assert execute_span["status"] == SpanStatus.OK
    else:
        events = capture_events()

        with huey.lock_task(lock_name):
            assert huey.is_locked(task_lock_name) == should_be_locked
            result = execute_huey_task(huey, maybe_locked_task)

        (event,) = events

        assert event["transaction"] == "maybe_locked_task"
        assert event["tags"]["huey_task_id"] == result.task.id
        assert (
            event["contexts"]["trace"]["status"] == "aborted"
            if should_be_locked
            else event["contexts"]["trace"]["status"] == "ok"
        )
    assert len(huey) == 0


def test_huey_enqueue(init_huey, capture_events):
    huey = init_huey()

    @huey.task(name="different_task_name")
    def dummy_task():
        pass

    events = capture_events()

    with start_transaction() as transaction:
        dummy_task()

    (event,) = events

    assert event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert event["contexts"]["trace"]["span_id"] == transaction.span_id

    assert len(event["spans"])
    assert event["spans"][0]["op"] == "queue.submit.huey"
    assert event["spans"][0]["description"] == "different_task_name"
    assert event["spans"][0]["data"][SPANDATA.MESSAGING_DESTINATION_NAME] == huey.name


def test_huey_propagate_trace(init_huey, capture_events):
    huey = init_huey()

    events = capture_events()

    @huey.task()
    def propagated_trace_task():
        pass

    with start_transaction() as outer_transaction:
        execute_huey_task(huey, propagated_trace_task)

    assert (
        events[0]["transaction"] == "propagated_trace_task"
    )  # the "inner" transaction
    assert events[0]["contexts"]["trace"]["trace_id"] == outer_transaction.trace_id


def test_span_origin_producer(init_huey, capture_events):
    huey = init_huey()

    @huey.task(name="different_task_name")
    def dummy_task():
        pass

    events = capture_events()

    with start_transaction():
        dummy_task()

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.queue.huey"


def test_span_origin_consumer(init_huey, capture_events):
    huey = init_huey()

    events = capture_events()

    @huey.task()
    def propagated_trace_task():
        pass

    execute_huey_task(huey, propagated_trace_task)

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "auto.queue.huey"


@pytest.mark.parametrize("has_span_streaming", [True, False])
@pytest.mark.skipif(HUEY_VERSION < (3, 0), reason="group was added in 3.0")
def test_huey_enqueue_group(
    init_huey, capture_events, capture_items, has_span_streaming
):
    huey = init_huey(has_span_streaming=has_span_streaming)

    @huey.task()
    def task1():
        pass

    @huey.task()
    def task2():
        pass

    if has_span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="submission"):
            huey.enqueue(group([task1.s(), task2.s()]))

        for _ in range(2):
            task = huey.dequeue()
            huey.execute(task)

        sentry_sdk.get_client().flush()
        assert len(items) == 6

        (
            task1_enqueue_span,
            task2_enqueue_span,
            group_span,
            submission_span,
            task1_execute_span,
            task2_execute_span,
        ) = [i.payload for i in items]

        # The enqueue happens inside a wrapping span, so the group producer
        # tree is created and parented under that segment.
        assert submission_span["is_segment"]
        assert submission_span["name"] == "submission"
        assert not group_span["is_segment"]
        assert not task1_enqueue_span["is_segment"]
        assert not task2_enqueue_span["is_segment"]
        assert task1_execute_span["is_segment"]
        assert task2_execute_span["is_segment"]

        assert group_span["parent_span_id"] == submission_span["span_id"]
        assert group_span["name"] == "Huey Task Group"
        assert group_span["status"] == "ok"
        assert group_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert group_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task1_enqueue_span["name"] == "task1"
        assert task1_enqueue_span["status"] == "ok"
        assert task1_enqueue_span["parent_span_id"] == group_span["span_id"]
        assert task1_enqueue_span["attributes"]["sentry.segment.name"] == "submission"
        assert task1_enqueue_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert task1_enqueue_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task2_enqueue_span["name"] == "task2"
        assert task2_enqueue_span["status"] == "ok"
        assert task2_enqueue_span["parent_span_id"] == group_span["span_id"]
        assert task2_enqueue_span["attributes"]["sentry.segment.name"] == "submission"
        assert task2_enqueue_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert task2_enqueue_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task1_execute_span["name"] == "task1"
        assert task1_execute_span["status"] == "ok"
        assert task1_execute_span["attributes"]["messaging.message.system"] == "huey"
        assert task1_execute_span["parent_span_id"] == task1_enqueue_span["span_id"]
        assert task1_execute_span["attributes"]["sentry.op"] == "queue.task.huey"
        assert task1_execute_span["attributes"]["sentry.origin"] == "auto.queue.huey"
        assert (
            task1_execute_span["attributes"]["sentry.span.source"] == SegmentSource.TASK
        )
        assert task1_execute_span["attributes"]["messaging.message.id"] is not None
        assert task1_execute_span["attributes"]["messaging.message.retry.count"] == 0

        assert task2_execute_span["name"] == "task2"
        assert task2_execute_span["status"] == "ok"
        assert task2_execute_span["parent_span_id"] == task2_enqueue_span["span_id"]
        assert task2_execute_span["attributes"]["messaging.message.system"] == "huey"
        assert task2_execute_span["attributes"]["sentry.op"] == "queue.task.huey"
        assert task2_execute_span["attributes"]["sentry.origin"] == "auto.queue.huey"
        assert (
            task2_execute_span["attributes"]["sentry.span.source"] == SegmentSource.TASK
        )

    else:
        events = capture_events()
        with start_transaction() as transaction:
            huey.enqueue(group([task1.s(), task2.s()]))

        for _ in range(2):
            task = huey.dequeue()
            huey.execute(task)

        assert len(events) == 3

        # Assert enqueue spans were successfully recorded
        producer_event = events[0]
        assert producer_event["type"] == "transaction"
        assert producer_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
        assert producer_event["contexts"]["trace"]["origin"] == "manual"

        spans = producer_event["spans"]
        assert len(spans) == 3
        assert spans[0]["op"] == "queue.submit.huey"
        assert spans[0]["description"] == "Huey Task Group"
        assert spans[1]["op"] == "queue.submit.huey"
        assert spans[1]["description"] == "task1"
        assert spans[2]["op"] == "queue.submit.huey"
        assert spans[2]["description"] == "task2"

        # Consumer transaction assertions (one per task)
        consumer_events = events[1:]
        for consumer_event, expected_name in zip(consumer_events, ["task1", "task2"]):
            assert consumer_event["type"] == "transaction"
            assert consumer_event["transaction"] == expected_name
            assert consumer_event["transaction_info"] == {"source": "task"}
            assert consumer_event["contexts"]["trace"]["op"] == "queue.task.huey"
            assert consumer_event["contexts"]["trace"]["origin"] == "auto.queue.huey"
            assert consumer_event["contexts"]["trace"]["status"] == "ok"
            assert (
                consumer_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
            )
            assert "huey_task_id" in consumer_event["tags"]
            assert consumer_event["tags"]["huey_task_retry"] is False


@pytest.mark.parametrize("has_span_streaming", [True, False])
@pytest.mark.skipif(HUEY_VERSION < (3, 0), reason="chord was added in 3.0")
def test_huey_enqueue_chord(
    init_huey, capture_events, capture_items, has_span_streaming
):
    huey = init_huey(has_span_streaming=has_span_streaming)

    @huey.task()
    def task1():
        pass

    @huey.task()
    def task2(results):
        pass

    if has_span_streaming:
        items = capture_items("span")
        with sentry_sdk.traces.start_span(name="submission"):
            huey.enqueue(chord([task1.s()], task2.s()))

        for _ in range(2):
            task = huey.dequeue()
            huey.execute(task)

        sentry_sdk.get_client().flush()
        assert len(items) == 6

        (
            task1_enqueue_span,
            chord_span,
            submission_span,
            task2_enqueue_span,
            task1_execute_span,
            task2_execute_span,
        ) = [i.payload for i in items]

        # The enqueue happens inside a wrapping span, so the chord producer
        # tree is created and parented under that segment.
        assert submission_span["is_segment"]
        assert submission_span["name"] == "submission"
        assert not chord_span["is_segment"]
        assert not task1_enqueue_span["is_segment"]
        assert not task2_enqueue_span["is_segment"]
        assert task1_execute_span["is_segment"]
        assert task2_execute_span["is_segment"]

        assert chord_span["parent_span_id"] == submission_span["span_id"]
        assert chord_span["name"] == "Huey Chord"
        assert chord_span["status"] == "ok"
        assert chord_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert chord_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task1_enqueue_span["name"] == "task1"
        assert task1_enqueue_span["status"] == "ok"
        assert task1_enqueue_span["parent_span_id"] == chord_span["span_id"]
        assert task1_enqueue_span["attributes"]["sentry.segment.name"] == "submission"
        assert task1_enqueue_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert task1_enqueue_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task1_execute_span["name"] == "task1"
        assert task1_execute_span["status"] == "ok"
        assert task1_execute_span["attributes"]["messaging.message.system"] == "huey"
        assert task1_execute_span["parent_span_id"] == task1_enqueue_span["span_id"]
        assert task1_execute_span["attributes"]["sentry.op"] == "queue.task.huey"
        assert task1_execute_span["attributes"]["sentry.origin"] == "auto.queue.huey"
        assert (
            task1_execute_span["attributes"]["sentry.span.source"] == SegmentSource.TASK
        )
        # chord callback (task2) is enqueued during task1's execution
        assert task2_enqueue_span["name"] == "task2"
        assert task2_enqueue_span["status"] == "ok"
        assert task2_enqueue_span["parent_span_id"] == task1_execute_span["span_id"]
        assert task2_enqueue_span["attributes"]["sentry.segment.name"] == "task1"
        assert task2_enqueue_span["attributes"]["sentry.op"] == "queue.submit.huey"
        assert task2_enqueue_span["attributes"]["sentry.origin"] == "auto.queue.huey"

        assert task2_execute_span["name"] == "task2"
        assert task2_execute_span["status"] == "ok"
        assert task2_execute_span["parent_span_id"] == task2_enqueue_span["span_id"]
        assert task2_execute_span["attributes"]["messaging.message.system"] == "huey"
        assert task2_execute_span["attributes"]["sentry.op"] == "queue.task.huey"
        assert task2_execute_span["attributes"]["sentry.origin"] == "auto.queue.huey"
        assert (
            task2_execute_span["attributes"]["sentry.span.source"] == SegmentSource.TASK
        )
    else:
        events = capture_events()
        with start_transaction() as transaction:
            huey.enqueue(chord([task1.s()], task2.s()))

        for _ in range(2):
            task = huey.dequeue()
            huey.execute(task)

        assert len(events) == 3

        # Enqueue spans
        producer_event = events[0]
        assert producer_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
        assert producer_event["contexts"]["trace"]["origin"] == "manual"

        spans = producer_event["spans"]
        assert len(spans) == 2
        assert spans[0]["op"] == "queue.submit.huey"
        assert spans[0]["description"] == "Huey Chord"
        assert spans[1]["op"] == "queue.submit.huey"
        assert spans[1]["description"] == "task1"

        task1_event = events[1]
        # Confirm the first task enqueued the chord callback
        assert len(task1_event["spans"]) == 1
        assert task1_event["spans"][0]["op"] == "queue.submit.huey"
        assert task1_event["spans"][0]["description"] == "task2"
        assert task1_event["type"] == "transaction"
        assert task1_event["transaction"] == "task1"
        assert task1_event["transaction_info"] == {"source": "task"}
        assert task1_event["contexts"]["trace"]["op"] == "queue.task.huey"
        assert task1_event["contexts"]["trace"]["origin"] == "auto.queue.huey"
        assert task1_event["contexts"]["trace"]["status"] == "ok"
        assert task1_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
        assert "huey_task_id" in task1_event["tags"]
        assert task1_event["tags"]["huey_task_retry"] is False

        task2_event = events[2]
        assert task2_event["type"] == "transaction"
        assert task2_event["transaction"] == "task2"
        assert task2_event["transaction_info"] == {"source": "task"}
        assert task2_event["contexts"]["trace"]["op"] == "queue.task.huey"
        assert task2_event["contexts"]["trace"]["origin"] == "auto.queue.huey"
        assert task2_event["contexts"]["trace"]["status"] == "ok"
        assert task2_event["contexts"]["trace"]["trace_id"] == transaction.trace_id
        assert "huey_task_id" in task2_event["tags"]
        assert task2_event["tags"]["huey_task_retry"] is False
