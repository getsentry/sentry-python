from decimal import DivisionByZero

import pytest
from huey import __version__ as HUEY_VERSION
from huey.api import MemoryHuey, Result
from huey.exceptions import CancelExecution, RetryTask

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.huey import HueyIntegration
from sentry_sdk.traces import SegmentNameSource, SpanStatus
from sentry_sdk.utils import parse_version
from tests.integrations.utils import DATA_COLLECTION_QUEUES_CASES

HUEY_VERSION = parse_version(HUEY_VERSION)

try:
    from huey.api import chord, group
except ImportError:
    chord = None
    group = None


@pytest.fixture
def init_huey(sentry_init):
    def inner(init_kwargs=None):
        sentry_init_kwargs = {
            "integrations": [HueyIntegration()],
            "traces_sample_rate": 1.0,
            "send_default_pii": True,
            "trace_lifecycle": "stream",
        }
        sentry_init_kwargs.update(init_kwargs or {})
        sentry_init(**sentry_init_kwargs)

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
def test_task_segment(capture_items, init_huey, task_fails):
    huey = init_huey()

    @huey.task()
    def division(a, b):
        return a / b

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
    assert execute_span["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME] == huey.name
    assert execute_span["name"] == "division"
    assert execute_span["status"] == (SpanStatus.ERROR if task_fails else SpanStatus.OK)


def test_task_retry(capture_items, init_huey):
    huey = init_huey()
    context = {"retry": True}

    @huey.task()
    def retry_task(context):
        if context["retry"]:
            context["retry"] = False
            raise RetryTask()

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


def test_task_cancel_does_not_override_status(capture_items, init_huey):
    huey = init_huey()

    @huey.task()
    def cancel_task():
        raise CancelExecution()

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


@pytest.mark.parametrize("lock_name", ["lock.a", "lock.b"], ids=["locked", "unlocked"])
@pytest.mark.skipif(HUEY_VERSION < (2, 5), reason="is_locked was added in 2.5")
def test_task_lock(capture_items, init_huey, lock_name):
    huey = init_huey()

    task_lock_name = "lock.a"
    should_be_locked = task_lock_name == lock_name

    @huey.task()
    @huey.lock_task(task_lock_name)
    def maybe_locked_task():
        pass

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
    assert len(huey) == 0


@pytest.mark.parametrize(
    "init_kwargs,expected_args,expected_kwargs",
    DATA_COLLECTION_QUEUES_CASES,
)
def test_task_args_kwargs_data_collection(
    capture_items,
    init_huey,
    init_kwargs,
    expected_args,
    expected_kwargs,
):
    huey = init_huey(init_kwargs=init_kwargs)

    @huey.task()
    def division(a, b):
        return a / b

    items = capture_items("event")
    execute_huey_task(huey, division, 1, b=0, exceptions=(DivisionByZero,))
    sentry_sdk.get_client().flush()
    events = [item.payload for item in items]
    (event,) = [event for event in events if "exception" in event]

    huey_job = event["extra"]["huey-job"]

    if expected_args is None:
        assert "args" not in huey_job
        assert "kwargs" not in huey_job
    else:
        assert huey_job["args"] == expected_args
        assert huey_job["kwargs"] == expected_kwargs


def test_huey_enqueue(init_huey, capture_items):
    huey = init_huey()

    @huey.task(name="different_task_name")
    def dummy_task():
        pass

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        dummy_task()

    sentry_sdk.get_client().flush()

    payloads = [i.payload for i in items]

    enqueue_span = next(
        p
        for p in payloads
        if p.get("attributes", {}).get("sentry.op") == OP.QUEUE_SUBMIT_HUEY
    )
    segment_span = next(p for p in payloads if p.get("is_segment"))

    assert enqueue_span["trace_id"] == segment_span["trace_id"]
    assert enqueue_span["parent_span_id"] == segment_span["span_id"]
    assert enqueue_span["name"] == "different_task_name"
    assert enqueue_span["attributes"]["sentry.op"] == OP.QUEUE_SUBMIT_HUEY
    assert enqueue_span["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME] == huey.name


def test_huey_propagate_trace(init_huey, capture_items):
    huey = init_huey()

    items = capture_items("span")

    @huey.task()
    def propagated_trace_task():
        pass

    with sentry_sdk.traces.start_span(name="producer"):
        execute_huey_task(huey, propagated_trace_task)

    sentry_sdk.get_client().flush()

    payloads = [i.payload for i in items]

    producer_span = next(p for p in payloads if p.get("name") == "producer")
    execute_span = next(
        p
        for p in payloads
        if p.get("attributes", {}).get("sentry.op") == OP.QUEUE_TASK_HUEY
    )

    assert execute_span["name"] == "propagated_trace_task"
    assert execute_span["trace_id"] == producer_span["trace_id"]


def test_span_origin_producer(init_huey, capture_items):
    huey = init_huey()

    @huey.task(name="different_task_name")
    def dummy_task():
        pass

    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="test"):
        dummy_task()

    sentry_sdk.get_client().flush()

    payloads = [i.payload for i in items]

    enqueue_span = next(
        p
        for p in payloads
        if p.get("attributes", {}).get("sentry.op") == OP.QUEUE_SUBMIT_HUEY
    )

    assert enqueue_span["attributes"]["sentry.origin"] == "auto.queue.huey"


def test_span_origin_consumer(init_huey, capture_items):
    huey = init_huey()

    items = capture_items("span")

    @huey.task()
    def propagated_trace_task():
        pass

    execute_huey_task(huey, propagated_trace_task)

    sentry_sdk.get_client().flush()

    payloads = [i.payload for i in items]

    execute_span = next(
        p
        for p in payloads
        if p.get("attributes", {}).get("sentry.op") == OP.QUEUE_TASK_HUEY
    )

    assert execute_span["attributes"]["sentry.origin"] == "auto.queue.huey"


@pytest.mark.skipif(HUEY_VERSION < (3, 0), reason="group was added in 3.0")
def test_huey_enqueue_group(init_huey, capture_items):
    huey = init_huey()

    @huey.task()
    def task1():
        pass

    @huey.task()
    def task2():
        pass

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
        task1_execute_span["attributes"]["sentry.segment.name.source"]
        == SegmentNameSource.TASK
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
        task2_execute_span["attributes"]["sentry.segment.name.source"]
        == SegmentNameSource.TASK
    )


@pytest.mark.skipif(HUEY_VERSION < (3, 0), reason="chord was added in 3.0")
def test_huey_enqueue_chord(init_huey, capture_items):
    huey = init_huey()

    @huey.task()
    def task1():
        pass

    @huey.task()
    def task2(results):
        pass

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
        task1_execute_span["attributes"]["sentry.segment.name.source"]
        == SegmentNameSource.TASK
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
        task2_execute_span["attributes"]["sentry.segment.name.source"]
        == SegmentNameSource.TASK
    )
