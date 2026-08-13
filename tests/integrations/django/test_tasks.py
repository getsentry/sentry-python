import pytest

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations.django import DjangoIntegration

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
def test_task_span_is_created(
    sentry_init,
    capture_items,
    immediate_backend,
):
    """Test that the queue.submit.django span is created when a task is enqueued."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        simple_task.enqueue()

    sentry_sdk.flush()
    spans = [item.payload for item in items]

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
    assert queue_submit_spans[0]["attributes"]["sentry.origin"] == "auto.http.django"


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
def test_task_enqueue_with_kwargs(
    sentry_init,
    immediate_backend,
    capture_items,
):
    """Test that task enqueuing works correctly with keyword arguments."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        result = greet.enqueue(name="World", greeting="Hi")

    assert result.return_value == "Hi, World!"

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    queue_submit_spans = [
        span
        for span in spans
        if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 1
    assert queue_submit_spans[0]["name"] == "tests.integrations.django.test_tasks.greet"


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_task_error_reporting(
    sentry_init,
    immediate_backend,
    capture_items,
):
    """Test that errors in tasks are correctly reported and don't break the span."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        result = failing_task.enqueue()

    with pytest.raises(ValueError, match="Task failed"):
        _ = result.return_value

    sentry_sdk.flush()
    spans = [item.payload for item in items]

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


@pytest.mark.skipif(
    not HAS_DJANGO_TASKS,
    reason="Django tasks are only available in Django 6.0+",
)
def test_multiple_task_enqueues_create_multiple_spans(
    sentry_init,
    capture_items,
    immediate_backend,
):
    """Test that enqueueing multiple tasks creates multiple spans."""
    sentry_init(
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    items = capture_items("span")

    with sentry_sdk.traces.start_span(name="custom parent"):
        task_one.enqueue()
        task_two.enqueue()
        task_one.enqueue()

    sentry_sdk.flush()
    spans = [item.payload for item in items]

    queue_submit_spans = [
        span
        for span in spans
        if span["attributes"].get("sentry.op") == OP.QUEUE_SUBMIT_DJANGO
    ]
    assert len(queue_submit_spans) == 3

    span_names = [span["name"] for span in queue_submit_spans]

    assert span_names.count("tests.integrations.django.test_tasks.task_one") == 2
    assert span_names.count("tests.integrations.django.test_tasks.task_two") == 1
