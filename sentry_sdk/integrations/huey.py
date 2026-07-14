import sys
from datetime import datetime
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.api import continue_trace, get_baggage, get_traceparent
from sentry_sdk.consts import OP, SPANDATA, SPANSTATUS
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import SegmentSource, SpanStatus, StreamedSpan
from sentry_sdk.tracing import (
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
    TransactionSource,
)
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    SENSITIVE_DATA_SUBSTITUTE,
    _register_control_flow_exception,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    reraise,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Optional, TypeVar, Union

    from sentry_sdk._types import Event, EventProcessor, Hint
    from sentry_sdk.utils import ExcInfo

    F = TypeVar("F", bound=Callable[..., Any])

try:
    from huey.api import Huey, PeriodicTask, Result, ResultGroup, Task
    from huey.exceptions import CancelExecution, RetryTask, TaskLockedException
except ImportError:
    raise DidNotEnable("Huey is not installed")

try:
    from huey.api import chord as HueyChord
    from huey.api import group as HueyGroup
except ImportError:
    HueyChord = None
    HueyGroup = None


HUEY_CONTROL_FLOW_EXCEPTIONS = (CancelExecution, RetryTask, TaskLockedException)


class HueyIntegration(Integration):
    identifier = "huey"
    origin = f"auto.queue.{identifier}"

    @staticmethod
    def setup_once() -> None:
        patch_enqueue()
        patch_execute()
        _register_control_flow_exception(
            [CancelExecution, RetryTask, TaskLockedException]
        )


def patch_enqueue() -> None:
    old_enqueue = Huey.enqueue

    @ensure_integration_enabled(HueyIntegration, old_enqueue)
    def _sentry_enqueue(
        self: "Huey", item: "Any"
    ) -> "Optional[Union[Result, ResultGroup]]":
        if HueyChord is not None and isinstance(item, HueyChord):
            span_name = "Huey Chord"
        elif HueyGroup is not None and isinstance(item, HueyGroup):
            span_name = "Huey Task Group"
        else:
            span_name = item.name

        is_span_streaming_enabled = has_span_streaming_enabled(
            sentry_sdk.get_client().options
        )

        span_ctx = None
        if is_span_streaming_enabled:
            span_ctx = sentry_sdk.traces.start_span(
                name=span_name,
                attributes={
                    "sentry.op": OP.QUEUE_SUBMIT_HUEY,
                    "sentry.origin": HueyIntegration.origin,
                    SPANDATA.MESSAGING_DESTINATION_NAME: self.name,
                },
            )
        else:
            span_ctx = sentry_sdk.start_span(
                op=OP.QUEUE_SUBMIT_HUEY,
                name=span_name,
                origin=HueyIntegration.origin,
            )
            span_ctx.set_data(SPANDATA.MESSAGING_DESTINATION_NAME, self.name)

        no_headers_types = (PeriodicTask,) + tuple(
            t for t in [HueyGroup, HueyChord] if t is not None
        )
        with span_ctx:
            if not isinstance(item, no_headers_types):
                # Attach trace propagation data to task kwargs. We do
                # not do this for periodic tasks, as these don't
                # really have an originating transaction.
                # Additionally, we do not do this for Huey groups or chords, as enqueue will
                # recursively call this method for each task within the list, resulting
                # in the trace propagation data being attached to each task individually
                # (which we want)
                item.kwargs["sentry_headers"] = {
                    BAGGAGE_HEADER_NAME: get_baggage(),
                    SENTRY_TRACE_HEADER_NAME: get_traceparent(),
                }
            return old_enqueue(self, item)

    Huey.enqueue = _sentry_enqueue


def _make_event_processor(task: "Any") -> "EventProcessor":
    def event_processor(event: "Event", hint: "Hint") -> "Optional[Event]":
        with capture_internal_exceptions():
            tags = event.setdefault("tags", {})
            tags["huey_task_id"] = task.id
            tags["huey_task_retry"] = task.default_retries > task.retries
            extra = event.setdefault("extra", {})
            extra["huey-job"] = {
                "task": task.name,
                "args": (
                    task.args
                    if should_send_default_pii()
                    else SENSITIVE_DATA_SUBSTITUTE
                ),
                "kwargs": (
                    task.kwargs
                    if should_send_default_pii()
                    else SENSITIVE_DATA_SUBSTITUTE
                ),
                "retry": (task.default_retries or 0) - task.retries,
            }

        return event

    return event_processor


def _capture_exception(exc_info: "ExcInfo") -> None:
    scope = sentry_sdk.get_current_scope()
    is_span_streaming_enabled = has_span_streaming_enabled(
        sentry_sdk.get_client().options
    )

    if exc_info[0] in HUEY_CONTROL_FLOW_EXCEPTIONS:
        if not is_span_streaming_enabled:
            scope.transaction.set_status(SPANSTATUS.ABORTED)
        elif type(scope._span) is StreamedSpan:
            scope._span._segment.status = SpanStatus.OK
        return

    if not is_span_streaming_enabled:
        scope.transaction.set_status(SPANSTATUS.INTERNAL_ERROR)
    elif type(scope._span) is StreamedSpan:
        scope._span._segment.status = SpanStatus.ERROR

    event, hint = event_from_exception(
        exc_info,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": HueyIntegration.identifier, "handled": False},
    )
    scope.capture_event(event, hint=hint)


def _wrap_task_execute(func: "F") -> "F":
    @ensure_integration_enabled(HueyIntegration, func)
    def _sentry_execute(*args: "Any", **kwargs: "Any") -> "Any":
        try:
            result = func(*args, **kwargs)
        except Exception:
            exc_info = sys.exc_info()
            _capture_exception(exc_info)
            reraise(*exc_info)

        return result

    return _sentry_execute  # type: ignore


def patch_execute() -> None:
    old_execute = Huey._execute

    @ensure_integration_enabled(HueyIntegration, old_execute)
    def _sentry_execute(
        self: "Huey", task: "Task", timestamp: "Optional[datetime]" = None
    ) -> "Any":
        with sentry_sdk.isolation_scope() as scope:
            with capture_internal_exceptions():
                scope._name = "huey"
                scope.clear_breadcrumbs()
                scope.add_event_processor(_make_event_processor(task))

            sentry_headers = task.kwargs.pop("sentry_headers", None)
            is_span_streaming_enabled = has_span_streaming_enabled(
                sentry_sdk.get_client().options
            )

            if is_span_streaming_enabled:
                headers = sentry_headers or {}
                sentry_sdk.traces.continue_trace(headers)
                span_ctx = sentry_sdk.traces.start_span(
                    name=task.name,
                    attributes={
                        "sentry.op": OP.QUEUE_TASK_HUEY,
                        "sentry.origin": HueyIntegration.origin,
                        "sentry.span.source": SegmentSource.TASK,
                        SPANDATA.MESSAGING_DESTINATION_NAME: self.name,
                        "messaging.message.id": task.id,
                        "messaging.message.system": "huey",
                        "messaging.message.retry.count": (task.default_retries or 0)
                        - task.retries,
                    },
                    parent_span=None,
                )
            else:
                transaction = continue_trace(
                    sentry_headers or {},
                    name=task.name,
                    op=OP.QUEUE_TASK_HUEY,
                    source=TransactionSource.TASK,
                    origin=HueyIntegration.origin,
                )
                transaction.set_status(SPANSTATUS.OK)
                span_ctx = sentry_sdk.start_transaction(transaction)
                span_ctx.set_data(SPANDATA.MESSAGING_DESTINATION_NAME, self.name)

            if not getattr(task, "_sentry_is_patched", False):
                task.execute = _wrap_task_execute(task.execute)
                task._sentry_is_patched = True

            with span_ctx:
                return old_execute(self, task, timestamp)

    Huey._execute = _sentry_execute
