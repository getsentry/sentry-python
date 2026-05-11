import pytest

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.consts import OP


try:
    from django.tasks import task

    HAS_DJANGO_TASKS = True
except ImportError:
    HAS_DJANGO_TASKS = False


@pytest.fixture
def immediate_backend(settings):
    """Configure Django to use the immediate task backend for synchronous testing."""
    settings.TASKS = {
        "default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}
    }


if HAS_DJANGO_TASKS:

    @task
    def simple_task():
        return "result"

    @task
    def add_numbers(a, b):
        return a + b

    @task
    def greet(name, greeting="Hello"):
        return f"{greeting}, {name}!"

    @task
    def failing_task():
        raise ValueError("Task failed!")

    @task
    def task_one():
        return 1

    @task
    def task_two():
        return 2


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_span_is_created(
    sentry_init,
    capture_events,
    capture_items,
    immediate_backend,
    span_streaming,
):
    """Test that the queue.submit.django span is created when a task is enqueued."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            simple_task.enqueue()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        queue_submit_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["name"]
            == "tests.integrations.django.test_tasks.simple_task"
        )
        assert (
            queue_submit_spans[0]["attributes"]["sentry.origin"] == "auto.http.django"
        )
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction"):
            simple_task.enqueue()

        (event,) = events
        assert event["type"] == "transaction"

        queue_submit_spans = [
            span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["description"]
            == "tests.integrations.django.test_tasks.simple_task"
        )
        assert queue_submit_spans[0]["origin"] == "auto.http.django"


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_task_enqueue_returns_result(sentry_init, immediate_backend):
    """Test that the task enqueuing behavior is unchanged from the user perspective."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )

    result = add_numbers.enqueue(3, 5)

    assert result is not None
    assert result.return_value == 8


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_enqueue_with_kwargs(
    sentry_init,
    immediate_backend,
    capture_events,
    capture_items,
    span_streaming,
):
    """Test that task enqueuing works correctly with keyword arguments."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            result = greet.enqueue(name="World", greeting="Hi")

        assert result.return_value == "Hi, World!"

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        queue_submit_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["name"]
            == "tests.integrations.django.test_tasks.greet"
        )
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction"):
            result = greet.enqueue(name="World", greeting="Hi")

        assert result.return_value == "Hi, World!"

        (event,) = events
        queue_submit_spans = [
            span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["description"]
            == "tests.integrations.django.test_tasks.greet"
        )


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_task_error_reporting(
    sentry_init,
    immediate_backend,
    capture_events,
    capture_items,
    span_streaming,
):
    """Test that errors in tasks are correctly reported and don't break the span."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            result = failing_task.enqueue()

            with pytest.raises(ValueError, match="Task failed"):
                _ = result.return_value

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        queue_submit_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
        ]

        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["name"]
            == "tests.integrations.django.test_tasks.failing_task"
        )
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction"):
            result = failing_task.enqueue()

            with pytest.raises(ValueError, match="Task failed"):
                _ = result.return_value

        assert len(events) == 2
        transaction_event = events[-1]
        assert transaction_event["type"] == "transaction"

        queue_submit_spans = [
            span
            for span in transaction_event["spans"]
            if span["op"] == OP.QUEUE_SUBMIT_DJANGO
        ]

        assert len(queue_submit_spans) == 1
        assert (
            queue_submit_spans[0]["description"]
            == "tests.integrations.django.test_tasks.failing_task"
        )


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
@pytest.mark.parametrize("span_streaming", [True, False])
def test_multiple_task_enqueues_create_multiple_spans(
    sentry_init,
    capture_events,
    capture_items,
    immediate_backend,
    span_streaming,
):
    """Test that enqueueing multiple tasks creates multiple spans."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
    )
    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            task_one.enqueue()
            task_two.enqueue()
            task_one.enqueue()

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        queue_submit_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 3

        span_names = [span["name"] for span in queue_submit_spans]
    else:
        events = capture_events()

        with sentry_sdk.start_transaction(name="test_transaction"):
            task_one.enqueue()
            task_two.enqueue()
            task_one.enqueue()

        (event,) = events
        queue_submit_spans = [
            span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
        ]
        assert len(queue_submit_spans) == 3

        span_names = [span["description"] for span in queue_submit_spans]

    assert span_names.count("tests.integrations.django.test_tasks.task_one") == 2
    assert span_names.count("tests.integrations.django.test_tasks.task_two") == 1
