import sys
from functools import wraps

import sentry_sdk
from sentry_sdk import isolation_scope
from sentry_sdk.api import continue_trace
from sentry_sdk.consts import OP
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.celery.beat import (
    _patch_beat_apply_entry,
    _patch_redbeat_maybe_due,
    _setup_celery_beat_signals,
)
from sentry_sdk.integrations.celery.utils import NoOpMgr, _now_seconds_since_epoch
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, TRANSACTION_SOURCE_TASK
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.scope import Scope
from sentry_sdk.utils import (
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    reraise,
)

if TYPE_CHECKING:
    from typing import Any
    from typing import Callable
    from typing import List
    from typing import Optional
    from typing import TypeVar
    from typing import Union

    from sentry_sdk._types import EventProcessor, Event, Hint, ExcInfo
    from sentry_sdk.tracing import Span

    F = TypeVar("F", bound=Callable[..., Any])


try:
    from celery import VERSION as CELERY_VERSION  # type: ignore
    from celery.app.trace import task_has_custom
    from celery.exceptions import (  # type: ignore
        Ignore,
        Reject,
        Retry,
        SoftTimeLimitExceeded,
    )
except ImportError:
    raise DidNotEnable("Celery not installed")


CELERY_CONTROL_FLOW_EXCEPTIONS = (Retry, Ignore, Reject)


class CeleryIntegration(Integration):
    identifier = "celery"

    def __init__(
        self,
        propagate_traces=True,
        monitor_beat_tasks=False,
        exclude_beat_tasks=None,
    ):
        # type: (bool, bool, Optional[List[str]]) -> None
        self.propagate_traces = propagate_traces
        self.monitor_beat_tasks = monitor_beat_tasks
        self.exclude_beat_tasks = exclude_beat_tasks

        if monitor_beat_tasks:
            _patch_beat_apply_entry()
            _patch_redbeat_maybe_due()
            _setup_celery_beat_signals()

    @staticmethod
    def setup_once():
        # type: () -> None
        if CELERY_VERSION < (4, 4, 7):
            raise DidNotEnable("Celery 4.4.7 or newer required.")

        _patch_build_tracer()
        _patch_task_apply_async()
        _patch_worker_exit()

        # This logger logs every status of every task that ran on the worker.
        # Meaning that every task's breadcrumbs are full of stuff like "Task
        # <foo> raised unexpected <bar>".
        ignore_logger("celery.worker.job")
        ignore_logger("celery.app.trace")

        # This is stdout/err redirected to a logger, can't deal with this
        # (need event_level=logging.WARN to reproduce)
        ignore_logger("celery.redirected")


def _set_status(status):
    # type: (str) -> None
    with capture_internal_exceptions():
        scope = Scope.get_current_scope()
        if scope.span is not None:
            scope.span.set_status(status)


def _capture_exception(task, exc_info):
    # type: (Any, ExcInfo) -> None
    client = sentry_sdk.get_client()
    if client.get_integration(CeleryIntegration) is None:
        return

    if isinstance(exc_info[1], CELERY_CONTROL_FLOW_EXCEPTIONS):
        # ??? Doesn't map to anything
        _set_status("aborted")
        return

    _set_status("internal_error")

    if hasattr(task, "throws") and isinstance(exc_info[1], task.throws):
        return

    event, hint = event_from_exception(
        exc_info,
        client_options=client.options,
        mechanism={"type": "celery", "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)


def _make_event_processor(task, uuid, args, kwargs, request=None):
    # type: (Any, Any, Any, Any, Optional[Any]) -> EventProcessor
    def event_processor(event, hint):
        # type: (Event, Hint) -> Optional[Event]

        with capture_internal_exceptions():
            tags = event.setdefault("tags", {})
            tags["celery_task_id"] = uuid
            extra = event.setdefault("extra", {})
            extra["celery-job"] = {
                "task_name": task.name,
                "args": args,
                "kwargs": kwargs,
            }

        if "exc_info" in hint:
            with capture_internal_exceptions():
                if issubclass(hint["exc_info"][0], SoftTimeLimitExceeded):
                    event["fingerprint"] = [
                        "celery",
                        "SoftTimeLimitExceeded",
                        getattr(task, "name", task),
                    ]

        return event

    return event_processor


def _wrap_apply_async(f):
    # type: (F) -> F
    """
    Apply_async is always called to put a task in the queue. This is called by the
    celery client (for example the Django project or the Celery Beat process)
    """

    @wraps(f)
    @ensure_integration_enabled(CeleryIntegration, f)
    def apply_async(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        task = args[0]

        # Do not create a span when the task is a Celery Beat task
        # (Because we do not have a transaction in that case)
        span_mgr = (
            sentry_sdk.start_span(op=OP.QUEUE_SUBMIT_CELERY, description=task.name)
            if not Scope.get_isolation_scope()._name == "celery-beat"
            else NoOpMgr()
        )  # type: Union[Span, NoOpMgr]

        with span_mgr as span:
            incoming_headers = kwargs.get("headers") or {}
            integration = sentry_sdk.get_client().get_integration(CeleryIntegration)

            # If Sentry Crons monitoring for Celery Beat tasks is enabled
            # add start timestamp of task,
            if integration is not None and integration.monitor_beat_tasks:
                incoming_headers.update(
                    {
                        "sentry-monitor-start-timestamp-s": "%.9f"
                        % _now_seconds_since_epoch(),
                    }
                )

            # Propagate Sentry trace information into the Celery task if desired
            default_propagate_traces = (
                integration.propagate_traces if integration is not None else True
            )
            propagate_traces = incoming_headers.pop(
                "sentry-propagate-traces", default_propagate_traces
            )

            if propagate_traces:
                with capture_internal_exceptions():
                    sentry_trace_headers = dict(
                        Scope.get_current_scope().iter_trace_propagation_headers(
                            span=span
                        )
                    )
                    # Set Sentry trace data in the headers of the Celery task
                    if sentry_trace_headers:
                        # Make sure we don't overwrite existing baggage
                        incoming_baggage = incoming_headers.get(BAGGAGE_HEADER_NAME)
                        sentry_baggage = sentry_trace_headers.get(BAGGAGE_HEADER_NAME)

                        combined_baggage = sentry_baggage or incoming_baggage
                        if sentry_baggage and incoming_baggage:
                            combined_baggage = "{},{}".format(
                                incoming_baggage,
                                sentry_baggage,
                            )

                        # Set Sentry trace data to the headers of the Celery task
                        incoming_headers.update(sentry_trace_headers)

                        if combined_baggage:
                            incoming_headers[BAGGAGE_HEADER_NAME] = combined_baggage

                        # Set sentry trace data also to the inner headers of the Celery task
                        # https://github.com/celery/celery/issues/4875
                        #
                        # Need to setdefault the inner headers too since other
                        # tracing tools (dd-trace-py) also employ this exact
                        # workaround and we don't want to break them.
                        incoming_headers.setdefault("headers", {}).update(
                            sentry_trace_headers
                        )
                        if combined_baggage:
                            incoming_headers["headers"][
                                BAGGAGE_HEADER_NAME
                            ] = combined_baggage

            # Add the Sentry options potentially added in `sentry_sdk.integrations.beat.sentry_apply_entry`
            # to the inner headers (done when auto-instrumenting Celery Beat tasks)
            # https://github.com/celery/celery/issues/4875
            #
            # Need to setdefault the inner headers too since other
            # tracing tools (dd-trace-py) also employ this exact
            # workaround and we don't want to break them.
            incoming_headers.setdefault("headers", {})
            for key, value in incoming_headers.items():
                if key.startswith("sentry-"):
                    incoming_headers["headers"][key] = value

            # Run the task (with updated headers in kwargs)
            kwargs["headers"] = incoming_headers

            return f(*args, **kwargs)

    return apply_async  # type: ignore


def _wrap_tracer(task, f):
    # type: (Any, F) -> F

    # Need to wrap tracer for pushing the scope before prerun is sent, and
    # popping it after postrun is sent.
    #
    # This is the reason we don't use signals for hooking in the first place.
    # Also because in Celery 3, signal dispatch returns early if one handler
    # crashes.
    @wraps(f)
    @ensure_integration_enabled(CeleryIntegration, f)
    def _inner(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        with isolation_scope() as scope:
            scope._name = "celery"
            scope.clear_breadcrumbs()
            scope.add_event_processor(_make_event_processor(task, *args, **kwargs))

            transaction = None

            # Celery task objects are not a thing to be trusted. Even
            # something such as attribute access can fail.
            with capture_internal_exceptions():
                headers = args[3].get("headers") or {}
                transaction = continue_trace(
                    headers,
                    op=OP.QUEUE_TASK_CELERY,
                    name="unknown celery task",
                    source=TRANSACTION_SOURCE_TASK,
                )
                transaction.name = task.name
                transaction.set_status("ok")

            if transaction is None:
                return f(*args, **kwargs)

            with sentry_sdk.start_transaction(
                transaction,
                custom_sampling_context={
                    "celery_job": {
                        "task": task.name,
                        # for some reason, args[1] is a list if non-empty but a
                        # tuple if empty
                        "args": list(args[1]),
                        "kwargs": args[2],
                    }
                },
            ):
                return f(*args, **kwargs)

    return _inner  # type: ignore


def _wrap_task_call(task, f):
    # type: (Any, F) -> F

    # Need to wrap task call because the exception is caught before we get to
    # see it. Also celery's reported stacktrace is untrustworthy.

    # functools.wraps is important here because celery-once looks at this
    # method's name.
    # https://github.com/getsentry/sentry-python/issues/421
    @wraps(f)
    def _inner(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        try:
            return f(*args, **kwargs)
        except Exception:
            exc_info = sys.exc_info()
            with capture_internal_exceptions():
                _capture_exception(task, exc_info)
            reraise(*exc_info)

    return _inner  # type: ignore


def _patch_build_tracer():
    # type: () -> None
    import celery.app.trace as trace  # type: ignore

    original_build_tracer = trace.build_tracer

    def sentry_build_tracer(name, task, *args, **kwargs):
        # type: (Any, Any, *Any, **Any) -> Any
        if not getattr(task, "_sentry_is_patched", False):
            # determine whether Celery will use __call__ or run and patch
            # accordingly
            if task_has_custom(task, "__call__"):
                type(task).__call__ = _wrap_task_call(task, type(task).__call__)
            else:
                task.run = _wrap_task_call(task, task.run)

            # `build_tracer` is apparently called for every task
            # invocation. Can't wrap every celery task for every invocation
            # or we will get infinitely nested wrapper functions.
            task._sentry_is_patched = True

        return _wrap_tracer(task, original_build_tracer(name, task, *args, **kwargs))

    trace.build_tracer = sentry_build_tracer


def _patch_task_apply_async():
    # type: () -> None
    from celery.app.task import Task  # type: ignore

    Task.apply_async = _wrap_apply_async(Task.apply_async)


def _patch_worker_exit():
    # type: () -> None

    # Need to flush queue before worker shutdown because a crashing worker will
    # call os._exit
    from billiard.pool import Worker  # type: ignore

    original_workloop = Worker.workloop

    def sentry_workloop(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        try:
            return original_workloop(*args, **kwargs)
        finally:
            with capture_internal_exceptions():
                if (
                    sentry_sdk.get_client().get_integration(CeleryIntegration)
                    is not None
                ):
                    sentry_sdk.flush()

    Worker.workloop = sentry_workloop
