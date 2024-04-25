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
from sentry_sdk.integrations.celery.utils import _now_seconds_since_epoch
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.tracing import BAGGAGE_HEADER_NAME, TRANSACTION_SOURCE_TASK
from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.scope import Scope
from sentry_sdk.tracing_utils import Baggage
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


def _update_celery_task_headers(original_headers, span, monitor_beat_tasks):
    # type: (dict[str, Any], Optional[Span], bool) -> dict[str, Any]
    """
    Updates the headers of the Celery task with the tracing information
    and eventually Sentry Crons monitoring information for beat tasks.
    """
    updated_headers = original_headers.copy()
    with capture_internal_exceptions():
        headers = {}
        if span is not None:
            headers = dict(
                Scope.get_current_scope().iter_trace_propagation_headers(span=span)
            )

        if monitor_beat_tasks:
            headers.update(
                {
                    "sentry-monitor-start-timestamp-s": "%.9f"
                    % _now_seconds_since_epoch(),
                }
            )

        if headers:
            existing_baggage = updated_headers.get(BAGGAGE_HEADER_NAME)
            sentry_baggage = headers.get(BAGGAGE_HEADER_NAME)

            combined_baggage = sentry_baggage or existing_baggage
            if sentry_baggage and existing_baggage:
                # Merge incoming and sentry baggage, where the sentry trace information
                # in the incoming baggage takes precedence and the third-party items
                # are concatenated.
                incoming = Baggage.from_incoming_header(existing_baggage)
                combined = Baggage.from_incoming_header(sentry_baggage)
                combined.sentry_items.update(incoming.sentry_items)
                combined.third_party_items = ",".join(
                    [
                        x
                        for x in [
                            combined.third_party_items,
                            incoming.third_party_items,
                        ]
                        if x is not None and x != ""
                    ]
                )
                combined_baggage = combined.serialize(include_third_party=True)

            updated_headers.update(headers)
            if combined_baggage:
                updated_headers[BAGGAGE_HEADER_NAME] = combined_baggage

            # https://github.com/celery/celery/issues/4875
            #
            # Need to setdefault the inner headers too since other
            # tracing tools (dd-trace-py) also employ this exact
            # workaround and we don't want to break them.
            updated_headers.setdefault("headers", {}).update(headers)
            if combined_baggage:
                updated_headers["headers"][BAGGAGE_HEADER_NAME] = combined_baggage

            # Add the Sentry options potentially added in `sentry_apply_entry`
            # to the headers (done when auto-instrumenting Celery Beat tasks)
            for key, value in updated_headers.items():
                if key.startswith("sentry-"):
                    updated_headers["headers"][key] = value

    return updated_headers


def _wrap_apply_async(f):
    # type: (F) -> F
    @wraps(f)
    @ensure_integration_enabled(CeleryIntegration, f)
    def apply_async(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        # Note: kwargs can contain headers=None, so no setdefault!
        # Unsure which backend though.
        kwarg_headers = kwargs.get("headers") or {}
        integration = sentry_sdk.get_client().get_integration(CeleryIntegration)
        propagate_traces = kwarg_headers.pop(
            "sentry-propagate-traces", integration.propagate_traces
        )

        if not propagate_traces:
            return f(*args, **kwargs)

        task = args[0]

        with sentry_sdk.start_span(
            op=OP.QUEUE_SUBMIT_CELERY, description=task.name
        ) as span:
            kwargs["headers"] = _update_celery_task_headers(
                kwarg_headers, span, integration.monitor_beat_tasks
            )
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
