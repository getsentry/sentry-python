from datetime import datetime, timedelta
from os import environ
import sys

from sentry_sdk.hub import Hub
from sentry_sdk._compat import reraise
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    logger,
    TimeoutThread,
)
from sentry_sdk.integrations import Integration

from sentry_sdk._types import MYPY

# Constants
TIMEOUT_WARNING_BUFFER = 1.5  # Buffer time required to send timeout warning to Sentry
MILLIS_TO_SECONDS = 1000.0

if MYPY:
    from typing import Any
    from typing import TypeVar
    from typing import Callable
    from typing import Optional

    from sentry_sdk._types import EventProcessor, Event, Hint

    F = TypeVar("F", bound=Callable[..., Any])


def _wrap_func(func):
    # type: (F) -> F
    def sentry_func(*args, **kwargs):
        # type: (*Any, **Any) -> Any

        hub = Hub.current
        integration = hub.get_integration(GcpIntegration)
        if integration is None:
            return func(*args, **kwargs)

        # If an integration is there, a client has to be there.
        client = hub.client  # type: Any

        configured_time = environ.get("FUNCTION_TIMEOUT_SEC")
        if not configured_time:
            logger.debug(
                "The configured timeout could not be fetched from Cloud Functions configuration."
            )
            return func(*args, **kwargs)

        configured_time = int(configured_time)

        initial_time = datetime.utcnow()

        with hub.push_scope() as scope:
            with capture_internal_exceptions():
                scope.clear_breadcrumbs()
                scope.transaction = environ.get("FUNCTION_NAME")
                scope.add_event_processor(
                    _make_request_event_processor(configured_time, initial_time)
                )
            try:
                if (
                    integration.timeout_warning
                    and configured_time > TIMEOUT_WARNING_BUFFER
                ):
                    waiting_time = configured_time - TIMEOUT_WARNING_BUFFER

                    timeout_thread = TimeoutThread(waiting_time, configured_time)

                    # Starting the thread to raise timeout warning exception
                    timeout_thread.start()
                return func(*args, **kwargs)
            except Exception:
                exc_info = sys.exc_info()
                event, hint = event_from_exception(
                    exc_info,
                    client_options=client.options,
                    mechanism={"type": "gcp", "handled": False},
                )
                hub.capture_event(event, hint=hint)
                reraise(*exc_info)
            finally:
                # Flush out the event queue
                hub.flush()

    return sentry_func  # type: ignore


class GcpIntegration(Integration):
    identifier = "gcp"

    def __init__(self, timeout_warning=False):
        # type: (bool) -> None
        self.timeout_warning = timeout_warning

    @staticmethod
    def setup_once():
        # type: () -> None
        import __main__ as gcp_functions  # type: ignore

        if not hasattr(gcp_functions, "worker_v1"):
            logger.warning(
                "GcpIntegration currently supports only Python 3.7 runtime environment."
            )
            return

        worker1 = gcp_functions.worker_v1

        worker1.FunctionHandler.invoke_user_function = _wrap_func(
            worker1.FunctionHandler.invoke_user_function
        )


def _make_request_event_processor(configured_timeout, initial_time):
    # type: (Any, Any) -> EventProcessor

    def event_processor(event, hint):
        # type: (Event, Hint) -> Optional[Event]

        final_time = datetime.utcnow()
        time_diff = final_time - initial_time

        execution_duration_in_millis = time_diff.microseconds / MILLIS_TO_SECONDS

        extra = event.setdefault("extra", {})
        extra["google cloud functions"] = {
            "function_name": environ.get("FUNCTION_NAME"),
            "function_entry_point": environ.get("ENTRY_POINT"),
            "function_identity": environ.get("FUNCTION_IDENTITY"),
            "function_region": environ.get("FUNCTION_REGION"),
            "function_project": environ.get("GCP_PROJECT"),
            "execution_duration_in_millis": execution_duration_in_millis,
            "configured_timeout_in_seconds": configured_timeout,
        }

        extra["google cloud logs"] = {
            "url": _get_google_cloud_logs_url(final_time),
        }

        request = event.get("request", {})

        request["url"] = "gcp:///{}".format(environ.get("FUNCTION_NAME"))

        event["request"] = request

        return event

    return event_processor


def _get_google_cloud_logs_url(final_time):
    # type: (datetime) -> str
    """
    Generates a Google Cloud Logs console URL based on the environment variables
    Arguments:
        final_time {datetime} -- Final time
    Returns:
        str -- Google Cloud Logs Console URL to logs.
    """
    hour_ago = final_time - timedelta(hours=1)
    formatstring = "%Y-%m-%dT%H:%M:%SZ"

    url = (
        "https://console.cloud.google.com/logs/viewer?project={project}&resource=cloud_function"
        "%2Ffunction_name%2F{function_name}%2Fregion%2F{region}&minLogLevel=0&expandAll=false"
        "&timestamp={timestamp_end}&customFacets=&limitCustomFacetWidth=true"
        "&dateRangeStart={timestamp_start}&dateRangeEnd={timestamp_end}"
        "&interval=PT1H&scrollTimestamp={timestamp_end}"
    ).format(
        project=environ.get("GCP_PROJECT"),
        function_name=environ.get("FUNCTION_NAME"),
        region=environ.get("FUNCTION_REGION"),
        timestamp_end=final_time.strftime(formatstring),
        timestamp_start=hour_ago.strftime(formatstring),
    )

    return url
