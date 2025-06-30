from __future__ import annotations
import inspect
import sys

import sentry_sdk
from sentry_sdk.consts import OP, SPANSTATUS
from sentry_sdk.integrations import _check_minimum_version, DidNotEnable, Integration
from sentry_sdk.tracing import TransactionSource
from sentry_sdk.utils import (
    event_from_exception,
    logger,
    package_version,
    qualname_from_function,
    reraise,
)

try:
    import ray  # type: ignore[import-not-found]
except ImportError:
    raise DidNotEnable("Ray not installed.")
import functools

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional
    from sentry_sdk.utils import ExcInfo

DEFAULT_TRANSACTION_NAME = "unknown Ray function"


def _check_sentry_initialized() -> None:
    if sentry_sdk.get_client().is_active():
        return

    logger.debug(
        "[Tracing] Sentry not initialized in ray cluster worker, performance data will be discarded."
    )


def _patch_ray_remote() -> None:
    old_remote = ray.remote

    @functools.wraps(old_remote)
    def new_remote(
        f: Optional[Callable[..., Any]] = None, *args: Any, **kwargs: Any
    ) -> Callable[..., Any]:

        if inspect.isclass(f):
            # Ray Actors
            # (https://docs.ray.io/en/latest/ray-core/actors.html)
            # are not supported
            # (Only Ray Tasks are supported)
            return old_remote(f, *args, **kwargs)

        def wrapper(user_f: Callable[..., Any]) -> Any:
            def new_func(
                *f_args: Any, _tracing: Optional[dict[str, Any]] = None, **f_kwargs: Any
            ) -> Any:
                _check_sentry_initialized()

                root_span_name = (
                    qualname_from_function(user_f) or DEFAULT_TRANSACTION_NAME
                )
                sentry_sdk.get_current_scope().set_transaction_name(
                    root_span_name,
                    source=TransactionSource.TASK,
                )
                with sentry_sdk.continue_trace(_tracing or {}):
                    with sentry_sdk.start_span(
                        op=OP.QUEUE_TASK_RAY,
                        name=qualname_from_function(user_f),
                        origin=RayIntegration.origin,
                        source=TransactionSource.TASK,
                    ) as root_span:
                        try:
                            result = user_f(*f_args, **f_kwargs)
                            root_span.set_status(SPANSTATUS.OK)
                        except Exception:
                            root_span.set_status(SPANSTATUS.INTERNAL_ERROR)
                            exc_info = sys.exc_info()
                            _capture_exception(exc_info)
                            reraise(*exc_info)

                        return result

            if f:
                rv = old_remote(new_func)
            else:
                rv = old_remote(*args, **kwargs)(new_func)
            old_remote_method = rv.remote

            def _remote_method_with_header_propagation(
                *args: Any, **kwargs: Any
            ) -> Any:
                """
                Ray Client
                """
                with sentry_sdk.start_span(
                    op=OP.QUEUE_SUBMIT_RAY,
                    name=qualname_from_function(user_f),
                    origin=RayIntegration.origin,
                    only_if_parent=True,
                ) as span:
                    tracing = {
                        k: v
                        for k, v in sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                    }
                    try:
                        result = old_remote_method(*args, **kwargs, _tracing=tracing)
                        span.set_status(SPANSTATUS.OK)
                    except Exception:
                        span.set_status(SPANSTATUS.INTERNAL_ERROR)
                        exc_info = sys.exc_info()
                        _capture_exception(exc_info)
                        reraise(*exc_info)

                    return result

            rv.remote = _remote_method_with_header_propagation

            return rv

        if f is not None:
            return wrapper(f)
        else:
            return wrapper

    ray.remote = new_remote


def _capture_exception(exc_info: ExcInfo, **kwargs: Any) -> None:
    client = sentry_sdk.get_client()

    event, hint = event_from_exception(
        exc_info,
        client_options=client.options,
        mechanism={
            "handled": False,
            "type": RayIntegration.identifier,
        },
    )
    sentry_sdk.capture_event(event, hint=hint)


class RayIntegration(Integration):
    identifier = "ray"
    origin = f"auto.queue.{identifier}"

    @staticmethod
    def setup_once() -> None:
        version = package_version("ray")
        _check_minimum_version(RayIntegration, version)

        _patch_ray_remote()
