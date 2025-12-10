import pytest

import sentry_sdk
from sentry_sdk import start_span
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
def test_task_span_is_created(sentry_init, capture_events, immediate_backend):
    """Test that the queue.submit.django span is created when a task is enqueued."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        simple_task.enqueue()

    (event,) = events
    assert event["type"] == "transaction"

    queue_submit_spans = [
        span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 1
    assert queue_submit_spans[0]["description"] == "simple_task"
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
def test_task_enqueue_with_kwargs(sentry_init, immediate_backend, capture_events):
    """Test that task enqueuing works correctly with keyword arguments."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        result = greet.enqueue(name="World", greeting="Hi")

    assert result.return_value == "Hi, World!"

    (event,) = events
    queue_submit_spans = [
        span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 1
    assert queue_submit_spans[0]["description"] == "greet"


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_task_error_reporting(sentry_init, immediate_backend, capture_events):
    """Test that errors in tasks are correctly reported and don't break the span."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        result = failing_task.enqueue()

    with pytest.raises(ValueError, match="Task failed"):
        _ = result.return_value

    assert len(events) >= 1
    transaction_event = events[-1]
    assert transaction_event["type"] == "transaction"

    queue_submit_spans = [
        span
        for span in transaction_event["spans"]
        if span["op"] == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 1
    assert queue_submit_spans[0]["description"] == "failing_task"


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_task_span_not_created_without_integration(
    sentry_init, capture_events, immediate_backend
):
    """Test that no span is created when DjangoIntegration is not enabled."""
    sentry_init(traces_sample_rate=1.0, default_integrations=False)
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        simple_task.enqueue()

    (event,) = events
    queue_submit_spans = [
        span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 0


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_multiple_task_enqueues_create_multiple_spans(
    sentry_init, capture_events, immediate_backend
):
    """Test that enqueueing multiple tasks creates multiple spans."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
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
    assert span_names.count("task_one") == 2
    assert span_names.count("task_two") == 1


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_nested_task_enqueue_spans(sentry_init, capture_events, immediate_backend):
    """Test that task spans are properly nested under parent spans."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
    )
    events = capture_events()

    with sentry_sdk.start_transaction(name="test_transaction"):
        with start_span(op="custom.operation", name="parent_span"):
            simple_task.enqueue()

    (event,) = events

    custom_spans = [span for span in event["spans"] if span["op"] == "custom.operation"]
    queue_submit_spans = [
        span for span in event["spans"] if span["op"] == OP.QUEUE_SUBMIT_DJANGO
    ]

    assert len(custom_spans) == 1
    assert len(queue_submit_spans) == 1

    assert queue_submit_spans[0]["parent_span_id"] == custom_spans[0]["span_id"]
