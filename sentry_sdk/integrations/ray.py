import logging

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.tracing import TRANSACTION_SOURCE_TASK
from sentry_sdk.utils import package_version

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

            with sentry_sdk.start_transaction(transaction) as transaction:
                result = f(*f_args, **f_kwargs)
                transaction.set_status("ok")
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
                    for k, v in sentry_sdk.get_current_scope().iter_trace_propagation_headers()
                }
                return old_remote_method(*args, **kwargs, _tracing=tracing)

        rv.remote = _remote_method_with_header_propagation

        return rv

    ray.remote = new_remote
    return


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
