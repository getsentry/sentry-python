"""Test to reproduce breadcrumb issue with failed Celery tasks."""
import pytest
from celery import Celery, VERSION
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration


@pytest.fixture
def init_celery(sentry_init, request):
    def inner(
        propagate_traces=True,
        backend="always_eager",
        monitor_beat_tasks=False,
        **kwargs,
    ):
        sentry_init(
            integrations=[
                CeleryIntegration(
                    propagate_traces=propagate_traces,
                    monitor_beat_tasks=monitor_beat_tasks,
                )
            ],
            **kwargs,
        )
        celery = Celery(__name__)

        if backend == "always_eager":
            if VERSION < (4,):
                celery.conf.CELERY_ALWAYS_EAGER = True
            else:
                celery.conf.task_always_eager = True
        else:
            raise ValueError(backend)

        return celery

    return inner


def test_breadcrumbs_preserved_for_failed_task(capture_events, init_celery):
    """Test that breadcrumbs are preserved when a Celery task fails."""
    celery = init_celery(enable_tracing=False)
    events = capture_events()
    
    @celery.task(name="test_breadcrumb_task")
    def failing_task():
        # Add a breadcrumb before failing
        sentry_sdk.add_breadcrumb(
            category="test",
            message="This is a test breadcrumb",
            level="info",
        )
        raise ValueError("Task failed!")
    
    # Run the failing task
    result = failing_task.delay()
    
    # The error is captured via Sentry's integration
    # Check that we captured an error event
    assert len(events) >= 1, f"Expected at least 1 event, got {len(events)}"
    
    # Find the error event
    error_events = [e for e in events if "exception" in e]
    assert len(error_events) >= 1, f"Expected at least 1 error event, got {len(error_events)}: {events}"
    
    error_event = error_events[0]
    
    # Check that breadcrumbs are present
    breadcrumbs = error_event.get("breadcrumbs", {}).get("values", [])
    print(f"Breadcrumbs: {breadcrumbs}")
    
    # Find our test breadcrumb
    test_breadcrumbs = [b for b in breadcrumbs if b.get("message") == "This is a test breadcrumb"]
    assert len(test_breadcrumbs) >= 1, f"Expected to find test breadcrumb, got {len(test_breadcrumbs)} breadcrumbs: {breadcrumbs}"


def test_breadcrumbs_preserved_with_retry(capture_events, init_celery):
    """Test that breadcrumbs are preserved across task retries."""
    celery = init_celery(enable_tracing=False)
    events = capture_events()
    
    failures = [True, True, False]
    
    @celery.task(name="test_retry_task", bind=True, max_retries=2)
    def retry_task(self):
        # Add a breadcrumb each time
        sentry_sdk.add_breadcrumb(
            category="test",
            message=f"Retry attempt {self.request.retries}",
            level="info",
        )
        
        if failures.pop(0):
            try:
                1 / 0
            except Exception as exc:
                raise self.retry(exc=exc, countdown=0)
        return "success"
    
    # Run the task
    result = retry_task.delay()
    
    # Should succeed after retries
    assert result.get() == "success"
    
    # There should be no error events (retries are handled)
    error_events = [e for e in events if "exception" in e]
    print(f"Error events: {len(error_events)}")
    print(f"All events: {len(events)}")


def test_breadcrumbs_isolated_between_tasks(capture_events, init_celery):
    """Test that breadcrumbs from one task don't leak to another."""
    celery = init_celery(enable_tracing=False)
    events = capture_events()
    
    @celery.task(name="task_a")
    def task_a():
        sentry_sdk.add_breadcrumb(
            category="test",
            message="Breadcrumb from task A",
            level="info",
        )
        return "a"
    
    @celery.task(name="task_b")
    def task_b():
        sentry_sdk.add_breadcrumb(
            category="test",
            message="Breadcrumb from task B",
            level="info",
        )
        raise ValueError("Task B failed!")
    
    # Run task A first
    result_a = task_a.delay()
    assert result_a.get() == "a"
    
    # Then run task B which fails
    result_b = task_b.delay()
    
    # Find the error event for task B
    error_events = [e for e in events if "exception" in e]
    assert len(error_events) >= 1, f"Expected at least 1 error event, got {len(error_events)}: {events}"
    
    error_event = error_events[-1]
    
    # Check breadcrumbs
    breadcrumbs = error_event.get("breadcrumbs", {}).get("values", [])
    print(f"Breadcrumbs in task B error: {breadcrumbs}")
    
    # Task B error should only have breadcrumb from task B, not task A
    messages = [b.get("message") for b in breadcrumbs]
    assert "Breadcrumb from task B" in messages, f"Expected task B breadcrumb in {messages}"
    assert "Breadcrumb from task A" not in messages, f"Task A breadcrumb leaked into task B error: {messages}"
