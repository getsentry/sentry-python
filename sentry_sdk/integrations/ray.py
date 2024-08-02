import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing import TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import logger, package_version, qualname_from_function

try:
    import ray  # type: ignore[import-not-found]
except ImportError:
    raise DidNotEnable("Ray not installed.")
import functools

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional


def _check_sentry_initialized():
    # type: () -> None
    if sentry_sdk.get_client().is_active():
        return

    logger.warning(
        "[Tracing] Sentry not initialized in ray cluster worker, performance data will be discarded."
    )


def _patch_ray_remote():
    # type: () -> None
    old_remote = ray.remote

    @functools.wraps(old_remote)
    def new_remote(f, *args, **kwargs):
        # type: (Callable[..., Any], *Any, **Any) -> Callable[..., Any]
        def _f(*f_args, _tracing=None, **f_kwargs):
            # type: (Any, Optional[dict[str, Any]],  Any) -> Any
            _check_sentry_initialized()

            transaction = sentry_sdk.continue_trace(
                _tracing or {},
                op=OP.QUEUE_TASK_RAY,
                name=qualname_from_function(f),
                origin=RayIntegration.origin,
                source=TRANSACTION_SOURCE_TASK,
            )

            with sentry_sdk.start_transaction(transaction) as transaction:
                result = f(*f_args, **f_kwargs)
                transaction.set_status("ok")
                return result

        rv = old_remote(_f, *args, *kwargs)
        old_remote_method = rv.remote

        def _remote_method_with_header_propagation(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with sentry_sdk.start_span(
                op=OP.QUEUE_SUBMIT_RAY,
                description=qualname_from_function(f),
                origin=RayIntegration.origin,
            ):
                tracing = {
                    k: v
                    for k, v in sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                }
                return old_remote_method(*args, **kwargs, _tracing=tracing)

        rv.remote = _remote_method_with_header_propagation

        return rv

    ray.remote = new_remote


class RayIntegration(Integration):
    identifier = "ray"
    origin = f"auto.queue.{identifier}"

    @staticmethod
    def setup_once():
        # type: () -> None
        version = package_version("ray")

        if version is None:
            raise DidNotEnable("Unparsable ray version: {}".format(version))

        if version < (2, 7, 0):
            raise DidNotEnable("Ray 2.7.0 or newer required")

        _patch_ray_remote()
