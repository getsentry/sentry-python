from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import ray  # type: ignore[import-not-found]
except ImportError:
    raise DidNotEnable("Ray not installed.")
import functools

from sentry_sdk.tracing import TRANSACTION_SOURCE_TASK
import logging
import sentry_sdk
from importlib.metadata import version

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional


def _check_sentry_initialized():
    # type: () -> None
    if sentry_sdk.Hub.current.client:
        return
    # we cannot use sentry sdk logging facilities because it wasn't initialized
    logger = logging.getLogger("sentry_sdk.errors")
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
            transaction = None
            if _tracing is not None:
                transaction = sentry_sdk.continue_trace(
                    _tracing,
                    op="ray.remote.receive",
                    source=TRANSACTION_SOURCE_TASK,
                    name="Ray worker transaction",
                )
            with sentry_sdk.start_transaction(transaction) as tx:
                result = f(*f_args, **f_kwargs)
                tx.set_status("ok")
                return result

        rv = old_remote(_f, *args, *kwargs)
        old_remote_method = rv.remote

        def _remote_method_with_header_propagation(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with sentry_sdk.start_span(
                op="ray.remote.send", description="Sending task to ray cluster."
            ):
                tracing = {
                    k: v
                    for k, v in sentry_sdk.Hub.current.iter_trace_propagation_headers()
                }
                return old_remote_method(*args, **kwargs, _tracing=tracing)

        rv.remote = _remote_method_with_header_propagation

        return rv

    ray.remote = new_remote
    return


class RayIntegration(Integration):
    identifier = "ray"

    @staticmethod
    def setup_once():
        # type: () -> None
        if tuple(int(x) for x in version("ray").split(".")) < (2, 7, 0):
            raise DidNotEnable("Ray 2.7.0 or newer required")
        _patch_ray_remote()
