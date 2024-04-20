import threading
import time
import urllib3

from enum import Enum
from typing import Any
from typing import Callable


class ErrorCode(Enum):
    """Error code encountered during feature evaluation.

    Returning error codes allows application developers to handle error
    responses intelligently without the need to parse error messages.
    """

    # The flag could not be found in the feature set. It might have
    # been deleted, disabled, or exists in a different environment.
    FLAG_NOT_FOUND = "FLAG_NOT_FOUND"
    # An error was raised that does not match one of the other error
    # codes.
    GENERAL = "GENERAL"
    # Something about the context object does not match the
    # requirements of the provider. This could mean a rule-set could
    # not be evaluated.
    INVALID_CONTEXT = "INVALID_CONTEXT"
    # We expected a response object to have a certain shape but it did
    # not. This can happen if the protocol is upgraded prior to the
    # SDK.
    PARSE_ERROR = "PARSE_ERROR"
    # The request made to the provider resolved but it returned an
    # error. We may have previously fetched a configuration that we
    # have cached in memory. Its okay to return the most recent value
    # with this error_code attached.
    PROVIDER_FATAL = "PROVIDER_FATAL"
    # We made a request to the remote feature provider but it hasn't
    # resolved yet.
    PROVIDER_NOT_READY = "PROVIDER_NOT_READY"
    # TODO: ...
    TARGETING_KEY_MISSING = "TARGETING_KEY_MISSING"
    # We expected a response value of type `T` but recieved of type of
    # not `T`. This could mean the caller has made an incorrect
    # assumption about a remote value's type or that the type was
    # changed on the server and the application has yet to be updated.
    TYPE_MISMATCH = "TYPE_MISMATCH"


class Reason(Enum):
    """The reason or source of a feature evaluation result.

    Reasons are required and must always be returned. Reasons allow
    application developers to make decisions about what level of trust
    to give a feature evaluation result. Some implementers may wish to
    be safe and never trust a stale feature. Others may choose to
    always trust the result of the evaluation.
    """

    # The source of the feature value was found in the cache. This
    # reason code is likely only appropriate in situations where we
    # loaded the features from disk.
    CACHED = "CACHED"
    # The application provided default was returned.
    DEFAULT = "DEFAULT"
    # The application provided default was returned because the feature
    # was disabled by the remote management system.
    DISABLED = "DISABLED"
    # The application provided default was returned because of an
    # error.
    ERROR = "ERROR"
    # The response value was the result of random or psuedo-random
    # assignment.
    SPLIT = "SPLIT"
    # The response value was pulled from the cache _and_ the most
    # recent request to the provider failed _or_ the provider has
    # closed and is no longer making requests to the remote management
    # system.
    STALE = "STALE"
    # The response value is statically defined in the configuration
    # with no variants attached.
    STATIC = "STATIC"
    # The response value is the result of a successful dynamic
    # evaluation of one of the variants.
    TARGETING_MATCH = "TARGETING_MATCH"
    # We have no idea where the value came from.
    UNKNOWN = "UNKNOWN"


class EvaluationResult:

    def __init__(
        self,
        reason,  # type: str
        value,  # type: str | int | bool | float | dict
        error_code=None,  # type: str | None
        error_message=None,  # type: str | None
        flag_metadata=None,  # type: dict[str, bool | int | str] | None
        variant=None,  # type: str | None
    ):
        self.error_code = error_code
        self.error_message = error_message
        self.flag_metadata = flag_metadata or {}
        self.reason = reason
        self.value = value
        self.variant = variant


# EVALUATION_CONTEXT = dict[str, "JSON_VALUE"]
# JSON_VALUE = (
#     bool | int | float | str | None | list["JSON_VALUE"] | dict[str, "JSON_VALUE"]
# )


class OpenFeatureProvider:
    """OpenFeature compatible provider interface."""

    def __init__(self, request_fn, poll_interval=60.0):
        # type: (Callable[[Callable[[Any], None], dict[str, str]], None], float) -> None
        self.provider = FeatureProvider(request_fn, poll_interval)

    def dispose(self):
        # type: () -> None
        self.provider.close()

    def resolveBooleanValue(self, flagKey, defaultValue, context):
        # type: (str, bool, dict[str, str]) -> EvaluationResult
        result = self.provider.get(
            flagKey, defaultValue, context=context, expected_type=bool
        )
        return result

    def resolveNumberValue(self, flagKey, defaultValue, context):
        # type: (str, int | float, dict[str, str]) -> EvaluationResult
        result = self.provider.get(
            flagKey, defaultValue, context=context, expected_type=(int, float)
        )
        return result

    def resolveStringValue(self, flagKey, defaultValue, context):
        # type: (str, str, dict[str, str]) -> EvaluationResult
        result = self.provider.get(
            flagKey, defaultValue, context=context, expected_type=str
        )
        return result

    def resolveStructureValue(self, flagKey, defaultValue, context):
        # type: (str, dict[str, str], dict[str, str]) -> EvaluationResult
        result = self.provider.get(
            flagKey, defaultValue, context=context, expected_type=dict
        )
        return result


class FeatureProvider:
    """Feature provider."""

    def __init__(self, request_fn, poll_interval=60.0):
        self.features = {}
        self.version = None
        self._lock = threading.Lock()
        self._task = PollResourceTask(self, request_fn, poll_interval)

    @property
    def closed(self):
        return self._task.closed

    @property
    def ready(self):
        return self._task.ready

    def close(self):
        self._task.close()

    def get(self, key, default, context, expected_type):
        # type: (str, Any, dict[str, bool | int | str], type | tuple[type, ...]) -> EvaluationResult
        # TODO: forces sequential reads.
        with self._lock:
            return self._evaluate_feature(key, default, context, expected_type)

    def update(self, response):
        # type: (urllib3.BaseHTTPResponse) -> None
        if response.status != 200:
            return None

        feature_set = response.json()
        features = {f["key"]: f for f in feature_set["features"]}

        with self._lock:
            self.version = feature_set["version"]
            self.features = features

    def wait(self, timeout):
        # type: (float) -> bool
        """Wait for a feature-set before proceeding."""
        return self._task.wait(timeout)

    def _evaluate_feature(self, key, default, context, expected_type=None):
        # type: (str, Any, dict[str, bool | float | int | str], type | tuple[type, ...] | None) -> None
        if not self.ready:
            return EvaluationResult(
                reason=Reason.ERROR,
                error_code=ErrorCode.PROVIDER_NOT_READY,
                value=default,
            )

        if key not in self.features:
            return EvaluationResult(
                reason=Reason.ERROR,
                error_code=ErrorCode.FLAG_NOT_FOUND,
                value=default,
            )

        # TODO: Version 0 extraction logic. No consideration for variants or rollout.
        value = self.features["value"]

        if expected_type is not None and not isinstance(value, expected_type):
            return EvaluationResult(
                reason=Reason.ERROR,
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

    :param provider:
    :param request_fn:
    :param poll_interval:
    """

    def __init__(
        self,
        provider,  # type: FeatureProvider
        request_fn,  # type: Callable[[Callable[[Any], None], dict[str, str]], None]
        poll_interval,  # type: float
        auto_start=True,  # type: bool
    ):
        self._closed = False
        self._fetch_event = threading.Event()
        self._last_fetch = 0
        self._provider = provider
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
            # type: (urllib3.BaseHTTPResponse) -> None
            resource_version = response.headers.get("ETag")
            if resource_version:
                self._resource_version = resource_version

            self._provider.update(response)
            self._fetch_event.set()

        self._request_fn(callback, headers={"ETag": self._resource_version})
        self._last_fetch = time.time()
        self._poll_count += 1

    def start(self):
        """Start the polling thread."""

        def poll_provider():
            while not self.closed:
                remaining = self.next_poll_time - time.time()
                if remaining <= 0:
                    self.poll()
                else:
                    time.sleep(remaining)

        self._thread = threading.Thread(target=poll_provider, daemon=True)
        self._thread.start()

    def wait(self, timeout):
        # type: (float) -> bool
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
