import threading
import time
import urllib3

from enum import Enum

from sentry_sdk._types import TYPE_CHECKING


if TYPE_CHECKING:
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
    # Currently unused. The evaluation context may define a special
    # "targeting_key" attribute for managing rollout in a deterministic
    # way. The most recent protocol definition uses a context attribute
    # defined explicitly within the feature.
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
        reason,  # type: Reason
        value,  # type: str | int | bool | float | dict
        error_code=None,  # type: ErrorCode | None
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

    def __init__(self, request_fn, poll_interval=60.0, auto_start=True):
        # type: (Callable[[Callable[[Any], None], dict[str, str]], None], float, bool) -> None
        self.state_machine = FeatureStateMachine(request_fn, poll_interval, auto_start)

    def dispose(self):
        # type: () -> None
        self.state_machine.close()

    def resolve_boolean_details(self, flag_key, default_value, context):
        # type: (str, bool, dict[str, str]) -> EvaluationResult
        return self._resolve_details(
            flag_key,
            default_value,
            context=context,
            expected_type=bool,
        )

    def resolve_integer_details(self, flag_key, default_value, context):
        # type: (str, int, dict[str, str]) -> EvaluationResult
        return self._resolve_details(
            flag_key,
            default_value,
            context=context,
            expected_type=int,
        )

    def resolve_float_details(self, flag_key, default_value, context):
        # type: (str, float, dict[str, str]) -> EvaluationResult
        return self._resolve_details(
            flag_key,
            default_value,
            context=context,
            expected_type=float,
        )

    def resolve_object_details(self, flag_key, default_value, context):
        # type: (str, dict, dict[str, str]) -> EvaluationResult
        return self._resolve_details(
            flag_key,
            default_value,
            context=context,
            expected_type=dict,
        )

    def resolve_string_details(self, flag_key, default_value, context):
        # type: (str, str, dict[str, str]) -> EvaluationResult
        return self._resolve_details(
            flag_key,
            default_value,
            context=context,
            expected_type=str,
        )

    def _resolve_details(
        self,
        key,  # type: str
        default,  # type: Any
        context,  # type: dict[str, bool | int | str]
        expected_type,  # type: type | tuple[type, ...]
    ):
        # type: (...) -> EvaluationResult
        return evaluate_feature(
            self.state_machine.state,
            key,
            default,
            context,
            expected_type,
        )


class FeatureStateMachine:
    """Feature finite state machine."""

    def __init__(self, request_fn, poll_interval=60.0, auto_start=True):
        self._state = Pending()
        self._lock = threading.Lock()
        self._task = PollResourceTask(self, request_fn, poll_interval, auto_start)

    @property
    def closed(self):
        return self._task.closed

    @property
    def state(self):
        # Because we're using immutable objects I believe this means we
        # do not have to lock here since we're just updating a pointer
        # on write. The instance will still exist and will be passed to
        # the evaluation context without a need to lock.
        #
        # TODO: Have someone smarter than me fact check.
        return self._state

    def close(self):
        self._task.close()

    def update(self, response):
        # type: (urllib3.BaseHTTPResponse) -> None
        with self._lock:
            self._state = _handle_state_update(self._state, response)

    def wait(self, timeout):
        # type: (float) -> bool
        """Wait for a feature-set before proceeding."""
        return self._task.wait(timeout)


class PollResourceTask:
    """HTTP resource polling task.

    This class asynchronously polls a remote resource until signaled to
    stop. On successful poll the resource identifier (ETag) is extraced
    and a response message is passed to the state machine.
    """

    def __init__(
        self,
        state_machine,  # type: FeatureStateMachine
        request_fn,  # type: Callable[[Callable[[Any], None], dict[str, str]], None]
        poll_interval,  # type: float
        auto_start=True,  # type: bool
    ):
        self._closed = False
        self._fetch_event = threading.Event()
        self._last_fetch = 0
        self._poll_count = 0
        self._refresh = poll_interval
        self._request_fn = request_fn

        # TODO: There's probably a better way to communicate than
        # embedding the state machine into the task. We could use
        # callbacks to remove the dependency.
        self._state_machine = state_machine

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
            self._state_machine.update(response)
            self._fetch_event.set()

        headers = {}
        if isinstance(
            self._state_machine._state, (Success, SuccessCached, FailureCached)
        ):
            headers["If-None-Match"] = self._state_machine._state.etag

        self._request_fn(callback, headers=headers)
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


# State machine utilities.


class _HasFeatures(object):
    def __init__(
        self,
        etag,  # type: str | None
        features,  # type: dict[str, Any]
        version,  # type: int
    ):
        self.etag = etag
        self.features = features
        self.version = version


class Pending(object):
    """Pending state containing no features."""

    pass


class Failure(object):
    """Failure state containing no features."""

    pass


class Success(_HasFeatures):
    """Success state containing fresh features."""

    pass


class SuccessCached(_HasFeatures):
    """Success state containing cached features."""

    pass


class FailureCached(_HasFeatures):
    """Failure state containing stale features."""

    pass


def _handle_state_update(
    old_state,  # type: Pending | Failure | FailureCached | Success | SuccessCached
    response,  # type: urllib3.BaseHTTPResponse
):
    # type: (...) -> Failure | FailureCached | Success | SuccessCached
    #
    # It is a type error to return a Pending state.
    if response.status == 200:
        # The request succeeded and returned content. We don't
        # care about the existing state. Everything is
        # overwritten with fresh data.
        feature_set = response.json()
        features = {f["key"]: f for f in feature_set["features"]}
        version = feature_set["version"]

        return Success(response.headers.get("ETag"), features, version)
    elif response.status == 304:
        return _handle_cached_response(old_state)
    else:
        return _handle_failure_response(old_state)


def _handle_cached_response(
    state,  # type: Pending | Failure | Success | SuccessCached | FailureCached
):
    if isinstance(state, (Success, SuccessCached, FailureCached)):
        # The request succeeded but returned no content.
        return SuccessCached(state.etag, state.features, state.version)
    else:
        # The request succeeded but returned no content. We don't have
        # any features in the cache and so can not make progress. Put
        # the provider in a failed state; something has gone wrong.
        return Failure()


def _handle_failure_response(
    state,  # type: Pending | Failure | Success | SuccessCached | FailureCached
):
    if isinstance(state, (Pending, Failure)):
        # The request failed and we were in a "Pending" or "Failure"
        # state. There are no cached features.
        return Failure()
    else:
        # The request failed but we have cached features. There features
        # are stale but we can still choose to use them.
        return FailureCached(state.etag, state.features, state.version)


# Feature evaluation.


def evaluate_feature(
    state,  # type: Pending | Failure | Success | SuccessCached | FailureCached
    key,  # type: str
    default,  # type: Any
    context,  # type: dict[str, bool | float | int | str]
    expected_type=None,  # type: type | tuple[type, ...] | None
):
    # Provider not ready because we're in a pending state which is
    # reserved for providers which either need to start or restart the
    # polling flow or wait until the request to the feature provider
    # has resolved.
    if isinstance(state, Pending):
        return EvaluationResult(
            reason=Reason.ERROR,
            error_code=ErrorCode.PROVIDER_NOT_READY,
            value=default,
        )

    # Provider fatal because we're in a failure state with no cached
    # flags.
    if isinstance(state, Failure):
        return EvaluationResult(
            reason=Reason.ERROR,
            error_code=ErrorCode.PROVIDER_FATAL,
            value=default,
        )

    # Flag doesn't exist in the set so we return not found.
    if key not in state.features:
        return EvaluationResult(
            reason=Reason.ERROR,
            error_code=ErrorCode.FLAG_NOT_FOUND,
            value=default,
        )

    # TODO: Pre-alpha extraction logic. No consideration for variants.
    value = state.features[key]["value"]

    # Type mismatch because the type returned by the feature evaluation
    # did not match the caller's expectations.
    #
    # TODO: Doesn't work with bool and int combos
    if not isinstance(value, expected_type):
        return EvaluationResult(
            reason=Reason.ERROR,
            error_code=ErrorCode.TYPE_MISMATCH,
            value=default,
        )

    # For each state which has cached features there is a lowest
    # prescedent reason which should be returned if no higher prescedent
    # reason was found. Basically, the reason should be unchanged unless
    # dynamic context evaluation was performed or some other error was
    # discovered.
    if isinstance(state, SuccessCached):
        default_reason = Reason.CACHED
    elif isinstance(state, Success):
        default_reason = Reason.STATIC
    elif isinstance(state, FailureCached):
        default_reason = Reason.STALE

    return EvaluationResult(
        reason=default_reason,
        error_code=None,
        value=value,
    )
