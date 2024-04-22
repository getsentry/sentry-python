from sentry_sdk.features import (
    OpenFeatureProvider,
    PollResourceTask,
    Reason,
    ErrorCode,
    Pending,
    Failure,
    Success,
    SuccessCached,
    FailureCached,
    evaluate_feature,
    _handle_state_update,
)
import time
import pytest


class HTTPResponse:

    def __init__(self, status, headers, content):
        self.status = status
        self.headers = headers
        self.content = content

    def json(self):
        return self.content


class StateMachine:
    def __init__(self, state):
        self.state = state

    def update(self, message):
        pass


# Test OpenFeatureProvider


@pytest.mark.parametrize(
    "method,value",
    [
        ("resolve_boolean_details", True),
        ("resolve_integer_details", 5),
        ("resolve_float_details", 5.0),
        ("resolve_object_details", {"hello": False}),
        ("resolve_string_details", "Hello, world!"),
    ],
)
def test_open_feature_resolve_details_static(method, value):
    state_machine = StateMachine(Success("", {"feature_name": {"value": value}}, 1))
    provider = OpenFeatureProvider(state_machine)

    details = getattr(provider, method)("feature_name", None, context={})
    assert details.error_code is None
    assert details.error_message is None
    assert details.flag_metadata == {}
    assert details.reason == Reason.STATIC
    assert details.value == value
    assert details.variant is None


@pytest.mark.parametrize(
    "method,default",
    [
        ("resolve_boolean_details", False),
        ("resolve_integer_details", 0),
        ("resolve_float_details", 0.0),
        ("resolve_object_details", {}),
        ("resolve_string_details", ""),
    ],
)
def test_open_feature_resolve_details_not_found(method, default):
    state_machine = StateMachine(Success("", {}, 1))
    provider = OpenFeatureProvider(state_machine)

    details = getattr(provider, method)("key", default, context={})
    assert details.error_code is ErrorCode.FLAG_NOT_FOUND
    assert details.error_message is None
    assert details.flag_metadata == {}
    assert details.reason == Reason.ERROR
    assert details.value == default
    assert details.variant is None


@pytest.mark.parametrize(
    "method,default",
    [
        ("resolve_boolean_details", False),
        ("resolve_integer_details", 0),
        ("resolve_float_details", 0.0),
        ("resolve_object_details", {}),
        ("resolve_string_details", ""),
    ],
)
def test_open_feature_resolve_details_not_ready(method, default):
    provider = OpenFeatureProvider(StateMachine(Pending()))

    details = getattr(provider, method)("key", default, context={})
    assert details.error_code is ErrorCode.PROVIDER_NOT_READY
    assert details.error_message is None
    assert details.flag_metadata == {}
    assert details.reason == Reason.ERROR
    assert details.value == default
    assert details.variant is None


@pytest.mark.parametrize(
    "method,default,value",
    [
        ("resolve_boolean_details", False, None),
        ("resolve_integer_details", 0, "hello"),
        ("resolve_float_details", 0.0, False),
        ("resolve_object_details", {}, "hello"),
        ("resolve_string_details", "", 0),
    ],
)
def test_open_feature_resolve_details_type_mismatch(method, default, value):
    state_machine = StateMachine(Success("", {"a": {"value": value}}, 1))
    provider = OpenFeatureProvider(state_machine)

    details = getattr(provider, method)("a", default, context={})
    assert details.error_code is ErrorCode.TYPE_MISMATCH
    assert details.error_message is None
    assert details.flag_metadata == {}
    assert details.reason == Reason.ERROR
    assert details.value == default
    assert details.variant is None


# Test PollResourceTask


def test_poll_resource_task_failure():
    """Assert on failure response the poll task marks itself as ready and waits."""

    def failure_response(callback, headers):
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(Pending()), failure_response, 30.0)
    assert task.ready
    assert not task.closed
    assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


def test_poll_resource_task_success():
    """Assert on success response the poll task marks itself as ready and waits."""

    def success_response(callback, headers):
        callback(HTTPResponse(200, {"ETag": "hello"}, {"version": 1, "features": []}))

    task = PollResourceTask(StateMachine(Pending()), success_response, 30.0)
    assert task.ready
    assert not task.closed
    assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


def test_poll_resource_wait_faster():
    """Assert responses faster than the blocking wait return true."""

    def fast_response(callback, headers):
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(Pending()), fast_response, 30.0)
    assert task.wait(timeout=0.001) is True


def test_poll_resource_wait_slower():
    """Assert responses slower than the blocking wait return false."""

    def slow_response(callback, headers):
        time.sleep(0.001)  # TODO: not ideal to sleep in a test.
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(Pending()), slow_response, 30.0)
    assert task.wait(timeout=0) is False


def test_poll_resource_close():
    """Assert a closed poll resource can no longer poll."""
    task = PollResourceTask(
        StateMachine(Pending()),
        lambda a, headers: a,
        30.0,
        auto_start=False,  # Prevents race condition with the automatically spawned thread.
    )
    task.close()
    task.poll()
    task.poll()

    assert task.closed
    assert task._poll_count == 0
    assert task.next_poll_time == 30.0
    assert not task.ready
    assert task.wait(timeout=1000) is True


def test_poll_resource_poll_throttled():
    """Assert calls to poll are throttled by the polling interval."""
    task = PollResourceTask(StateMachine(Pending()), lambda a, headers: a, 0.001)
    assert task._poll_count == 1

    time.sleep(0.0011)  # TODO: not ideal to sleep in a test.
    assert task._poll_count == 2

    time.sleep(0.0021)  # TODO: not ideal to sleep in a test.
    assert task._poll_count == 4


# Test state machine.


@pytest.mark.parametrize(
    "state,response,expected",
    [
        # Pending state transitions.
        (Pending(), HTTPResponse(200, {}, {"features": [], "version": 1}), Success),
        (Pending(), HTTPResponse(304, {}, {}), Failure),
        (Pending(), HTTPResponse(400, {}, {}), Failure),
        # Failure state transitions.
        (Failure(), HTTPResponse(200, {}, {"features": [], "version": 1}), Success),
        (Failure(), HTTPResponse(304, {}, {}), Failure),
        (Failure(), HTTPResponse(400, {}, {}), Failure),
        # Failure cached state transitions.
        (
            FailureCached("", {}, 0),
            HTTPResponse(200, {}, {"features": [], "version": 1}),
            Success,
        ),
        (FailureCached("", {}, 0), HTTPResponse(304, {}, {}), SuccessCached),
        (FailureCached("", {}, 0), HTTPResponse(400, {}, {}), FailureCached),
        # Success cached state transitions.
        (
            SuccessCached("", {}, 0),
            HTTPResponse(200, {}, {"features": [], "version": 1}),
            Success,
        ),
        (SuccessCached("", {}, 0), HTTPResponse(304, {}, {}), SuccessCached),
        (SuccessCached("", {}, 0), HTTPResponse(400, {}, {}), FailureCached),
        # Success state transitions.
        (
            Success("", {}, 0),
            HTTPResponse(200, {}, {"features": [], "version": 1}),
            Success,
        ),
        (Success("", {}, 0), HTTPResponse(304, {}, {}), SuccessCached),
        (Success("", {}, 0), HTTPResponse(400, {}, {}), FailureCached),
    ],
)
def test_handle_state_update(state, response, expected):
    """Test state transitions from HTTP response."""
    result = _handle_state_update(state, response)
    assert isinstance(result, expected)

    if isinstance(result, (FailureCached, SuccessCached)):
        # A result in a cached state must have previously been in a
        # cached state or a success state.
        assert isinstance(state, (FailureCached, Success, SuccessCached))

        # Assert cached states copy the previous state's internals.
        assert result.etag == state.etag
        assert result.features == state.features
        assert result.version == state.version

    # We never transition to a Pending state in the update flow.
    assert not isinstance(expected, Pending)


# Test feature evaluation.


def _simple_string_feature(key, value):
    return {key: {"key": key, "value": value}}


@pytest.mark.parametrize(
    "state,error_code,reason,value",
    [
        (
            Pending(),
            ErrorCode.PROVIDER_NOT_READY,
            Reason.ERROR,
            "default",
        ),
        (
            Failure(),
            ErrorCode.PROVIDER_FATAL,
            Reason.ERROR,
            "default",
        ),
        (
            FailureCached(None, _simple_string_feature("key", "value"), 0),
            None,
            Reason.STALE,
            "value",
        ),
        (
            Success(None, _simple_string_feature("key", "value"), 0),
            None,
            Reason.STATIC,
            "value",
        ),
        (
            SuccessCached(None, _simple_string_feature("key", "value"), 0),
            None,
            Reason.CACHED,
            "value",
        ),
    ],
)
def test_evaluate_feature_simple_string_feature(state, error_code, reason, value):
    """Assert simple string feature evaluation for each state.

    Simple in this case means a strict key, value pairing with no variants.
    """
    result = evaluate_feature(
        state=state,
        key="key",
        default="default",
        context={},
        expected_type=str,
    )

    assert result.error_code == error_code
    assert result.reason == reason
    assert result.value == value


@pytest.mark.parametrize(
    "state",
    [
        Success,
        SuccessCached,
        FailureCached,
    ],
)
def test_evaluate_feature_type_mismatch(state):
    """Assert mismatched types return the default an error code."""
    result = evaluate_feature(
        state=state(None, _simple_string_feature("key", "value"), 0),
        key="key",
        default=0,
        context={},
        expected_type=int,
    )
    assert result.error_code == ErrorCode.TYPE_MISMATCH
    assert result.reason == Reason.ERROR
    assert result.value == 0
