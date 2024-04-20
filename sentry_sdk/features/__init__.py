import functools
import threading
import time
import requests

from enum import Enum
from typing import Any


class ErrorCode(Enum):
    FLAG_NOT_FOUND = 0
    GENERAL = 1
    INVALID_CONTEXT = 2
    PARSE_ERROR = 3
    PROVIDER_FATAL = 4
    PROVIDER_NOT_READY = 5
    TARGETING_KEY_MISSING = 6
    TYPE_MISMATCH = 7


class Reason(Enum):
    CACHED = 0
    DEFAULT = 1
    DISABLED = 2
    ERROR = 3
    SPLIT = 4
    STALE = 5
    STATIC = 6
    TARGETING_MATCH = 7
    UNKNOWN = 8


class EvaluationResult:

    def __init__(
        self,
        reason,
        value,
        error_code=None,
        error_message=None,
        flag_metadata={},
        variant=None,
    ):
        # type: (dict[str, str | int | bool], str, Any, str | None, str | None, str | None) -> None
        self.error_code = error_code
        self.error_message = error_message
        self.flag_metadata = flag_metadata
        self.reason = reason
        self.value = value
        self.variant = variant


# EVALUATION_CONTEXT = dict[str, "JSON_VALUE"]

# FLAG_METADATA = dict[str, str | int | bool]

# JSON_VALUE = (
#     bool | int | float | str | None | list["JSON_VALUE"] | dict[str, "JSON_VALUE"]
# )

# class ResolutionDetails(Generic[T]):
#     error_code: ERROR_CODES | None
#     error_message: str | None
#     flag_metadata: FLAG_METADATA
#     reason: REASONS | str | None
#     value: T
#     variant: str | None


class FeatureProvider:
    """Feature provider interface.

    OpenFeature compatible feature evaluation interface.
    """

    def __init__(self, url, name, poll_interval=60.0):
        # type: (str, str, float) -> None
        request_fn = functools.partial(_fetch_feature_set, url, name)
        self._manager = FeatureManager(request_fn, poll_interval)

    def dispose(self):
        # type: () -> None
        self._manager.close()

    def resolveBooleanValue(self, flagKey, defaultValue, context):
        # type: (str, bool, dict[str, str]) -> EvaluationResult
        result = self._manager.get(
            flagKey, defaultValue, context=context, expected_types=(bool,)
        )
        return result

    def resolveNumberValue(self, flagKey, defaultValue, context):
        # type: (str, int | float, dict[str, str]) -> EvaluationResult
        result = self._manager.get(
            flagKey, defaultValue, context=context, expected_types=(int, float)
        )
        return result

    def resolveStringValue(self, flagKey, defaultValue, context):
        # type: (str, str, dict[str, str]) -> EvaluationResult
        result = self._manager.get(
            flagKey, defaultValue, context=context, expected_types=(str,)
        )
        return result

    def resolveStructureValue(self, flagKey, defaultValue, context):
        # type: (str, dict[str, str], dict[str, str]) -> EvaluationResult
        result = self._manager.get(
            flagKey, defaultValue, context=context, expected_types=(dict,)
        )
        return result


class FeatureManager:
    """Feature manager."""

    def __init__(self, request_fn, poll_interval=60.0):
        self.features = {}
        self.version = None
        self._lock = threading.Lock()
        self._task = PollResourceTask(self, request_fn, poll_interval)

    @property
    def is_ready(self):
        return self._task.is_ready

    def close(self):
        self._task.close()

    def get(self, key, default, context={}):
        # TODO: forces sequential reads.
        with self._lock:
            return self._evaluate_feature(key, default, context)

    def update(self, response) -> None:
        if response.status_code != 200:
            return None

        feature_set = response.json()
        features = {f["key"]: f for f in feature_set["features"]}

        with self._lock:
            self.version = feature_set["version"]
            self.features = features

    def wait(self, timeout):
        """Wait for a feature-set before proceeding."""
        return self._task.wait(timeout)

    def _evaluate_feature(self, key, default, context, expected_types):
        if not self.is_ready:
            return EvaluationResult(
                reason=Reason.DEFAULT,
                error_code=ErrorCode.PROVIDER_NOT_READY,
                value=default,
            )

        if key not in self.features:
            return EvaluationResult(
                reason=Reason.DEFAULT,
                error_code=ErrorCode.FLAG_NOT_FOUND,
                value=default,
            )

        value = self.features["value"]

        if not isinstance(value, expected_types):
            return EvaluationResult(
                reason=Reason.DEFAULT,
                error_code=ErrorCode.TYPE_MISMATCH,
                value=default,
            )

        return EvaluationResult(
            reason=Reason.TARGETING_MATCH,
            error_code=None,
            value=value,
        )


class PollResourceTask:
    """HTTP resource polling task.

    This class asynchronously polls a remote resource until signaled to
    stop. On successful poll the resource identifier (ETag) is extraced
    and a response message is passed to the state machine.

    :param state_machine:
    :param request_fn:
    :param poll_interval:
    """

    def __init__(
        self, state_machine, request_fn, poll_interval, auto_start=True
    ) -> None:
        self._closed = False
        self._fetch_event = threading.Event()
        self._last_fetch = 0
        self._state_machine = state_machine
        self._poll_count = 0
        self._refresh = poll_interval
        self._request_fn = request_fn
        self._resource_version = None

        if auto_start:
            self.start()

    @property
    def closed(self):
        """Return True if the task has been closed."""
        return self._closed

    @property
    def next_poll_time(self):
        """Return a UNIX timestamp representing the next available poll time."""
        return self._last_fetch + self._refresh

    @property
    def ready(self):
        return self._fetch_event.is_set()

    def close(self):
        """Stop all communication with the feature provider."""
        self._closed = True

    def poll(self):
        """Poll the feature provider.

        If the request-manager has been closed do nothing. Otherwise we
        fetch the feature-set from the provider.
        """
        if self.closed:
            return None

        def callback(response):
            self._resource_version = _get_resource_version(response)
            self._state_machine.update(response)
            self._fetch_event.set()

        self._request_fn(callback, headers={"ETag": self._resource_version})
        self._last_fetch = time.time()
        self._poll_count += 1

    def start(self):
        """Start the polling thread."""

        def poll_provider():
            while not self.closed:
                if self.next_poll_time < time.time():
                    self.poll()
                else:
                    time.sleep(1)

        self._thread = threading.Thread(target=poll_provider, daemon=True)
        self._thread.start()

    def wait(self, timeout):
        """Wait for the initial polling operation to complete.

        We only wait for the initial feature-set fetch to complete. Once
        the event has been set subsequent fetches resolve in the
        background.

        If the request-manager has been closed we eagerly exit.
        """
        if self.closed:
            return True
        else:
            return self._fetch_event.wait(timeout)


def _get_resource_version(response):
    """Return the version identifier for the feature-set."""
    if response.status_code == 200:
        return response["ETag"]


def _fetch_feature_set(url, name, callback, headers):
    """Fetch the named feature-set from the provider."""
    response = requests.get(url + f"?name={name}", headers=headers)
    callback(response)
