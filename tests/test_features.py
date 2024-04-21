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
    def __init__(self):
        self._state = Pending()

    def update(self, message):
        pass


# Test OpenFeatureProvider


# @pytest.mark.parametrize(
#     "method,value",
#     [
#         ("resolve_boolean_details", True),
#         ("resolve_integer_details", 5),
#         ("resolve_float_details", 5.0),
#         ("resolve_object_details", {"hello": False}),
#         ("resolve_string_details", "Hello, world!"),
#     ],
# )
# def test_open_feature_resolve_details_static(method, value):
#     def request_fn(callback, headers):
#         callback(
#             HTTPResponse(
#                 200,
#                 {"ETag": "hello"},
#                 {"version": 1, "features": [{"key": "feature_name", "value": value}]},
#             )
#         )

#     # Synchronous flag resolution.
#     provider = OpenFeatureProvider(request_fn, auto_start=False)
#     provider.provider._task.poll()

#     details = getattr(provider, method)("feature_name", None, context={})
#     assert details.error_code is None
#     assert details.error_message is None
#     assert details.flag_metadata == {}
#     assert details.reason == Reason.STATIC
#     assert details.value == value
#     assert details.variant is None


# @pytest.mark.parametrize(
#     "method,default",
#     [
#         ("resolve_boolean_details", False),
#         ("resolve_integer_details", 0),
#         ("resolve_float_details", 0.0),
#         ("resolve_object_details", {}),
#         ("resolve_string_details", ""),
#     ],
# )
# def test_open_feature_resolve_details_not_found(method, default):
#     def request_fn(callback, headers):
#         callback(HTTPResponse(200, {"ETag": "hello"}, {"version": 1, "features": []}))

#     # Synchronous flag resolution.
#     provider = OpenFeatureProvider(request_fn, auto_start=False)
#     provider.provider._task.poll()

#     details = getattr(provider, method)("key", default, context={})
#     assert details.error_code is ErrorCode.FLAG_NOT_FOUND
#     assert details.error_message is None
#     assert details.flag_metadata == {}
#     assert details.reason == Reason.ERROR
#     assert details.value == default
#     assert details.variant is None


# @pytest.mark.parametrize(
#     "method,default",
#     [
#         ("resolve_boolean_details", False),
#         ("resolve_integer_details", 0),
#         ("resolve_float_details", 0.0),
#         ("resolve_object_details", {}),
#         ("resolve_string_details", ""),
#     ],
# )
# def test_open_feature_resolve_details_not_ready(method, default):
#     def request_fn(callback, headers):
#         callback(HTTPResponse(200, {"ETag": "hello"}, {"version": 1, "features": []}))

#     # Synchronous flag resolution.
#     provider = OpenFeatureProvider(request_fn, auto_start=False)
#     # Explicitly commented to demonstrate the poll method has not been called.
#     # provider.provider._task.poll()

#     details = getattr(provider, method)("key", default, context={})
#     assert details.error_code is ErrorCode.PROVIDER_NOT_READY
#     assert details.error_message is None
#     assert details.flag_metadata == {}
#     assert details.reason == Reason.ERROR
#     assert details.value == default
#     assert details.variant is None


# @pytest.mark.parametrize(
#     "method,default,value",
#     [
#         ("resolve_boolean_details", False, None),
#         ("resolve_integer_details", 0, "hello"),
#         ("resolve_float_details", 0.0, False),
#         ("resolve_object_details", {}, "hello"),
#         ("resolve_string_details", "", 0),
#     ],
# )
# def test_open_feature_resolve_details_type_mismatch(method, default, value):
#     def request_fn(callback, headers):
#         callback(
#             HTTPResponse(
#                 200,
#                 {"ETag": "hello"},
#                 {"version": 1, "features": [{"key": "a", "value": value}]},
#             )
#         )

#     # Synchronous flag resolution.
#     provider = OpenFeatureProvider(request_fn, auto_start=False)
#     provider.provider._task.poll()

#     details = getattr(provider, method)("a", default, context={})
#     assert details.error_code is ErrorCode.TYPE_MISMATCH
#     assert details.error_message is None
#     assert details.flag_metadata == {}
#     assert details.reason == Reason.ERROR
#     assert details.value == default
#     assert details.variant is None


# Test PollResourceTask


# def test_poll_resource_task_failure():
#     """Assert on failure response the poll task marks itself as ready and waits."""

#     def failure_response(callback, headers):
#         callback(HTTPResponse(400, {"ETag": "hello"}, ""))

#     task = PollResourceTask(StateMachine(), failure_response, 30.0)
#     assert task.ready
#     assert not task.closed
#     assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


# def test_poll_resource_task_success():
#     """Assert on success response the poll task marks itself as ready and waits."""

#     def success_response(callback, headers):
#         callback(HTTPResponse(200, {"ETag": "hello"}, {"version": 1, "features": []}))

#     task = PollResourceTask(StateMachine(), success_response, 30.0)
#     assert task.ready
#     assert not task.closed
#     assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


# def test_poll_resource_wait_faster():
#     """Assert responses faster than the blocking wait return true."""

#     def fast_response(callback, headers):
#         callback(HTTPResponse(400, {"ETag": "hello"}, ""))

#     task = PollResourceTask(StateMachine(), fast_response, 30.0)
#     assert task.wait(timeout=0.001) is True


# def test_poll_resource_wait_slower():
#     """Assert responses slower than the blocking wait return false."""

#     def slow_response(callback, headers):
#         time.sleep(0.001)  # TODO: not ideal to sleep in a test.
#         callback(HTTPResponse(400, {"ETag": "hello"}, ""))

#     task = PollResourceTask(StateMachine(), slow_response, 30.0)
#     assert task.wait(timeout=0) is False


# def test_poll_resource_close():
#     """Assert a closed poll resource can no longer poll."""
#     task = PollResourceTask(
#         StateMachine(),
#         lambda a, headers: a,
#         30.0,
#         auto_start=False,  # Prevents race condition with the automatically spawned thread.
#     )
#     task.close()
#     task.poll()
#     task.poll()

#     assert task.closed
#     assert task._poll_count == 0
#     assert task.next_poll_time == 30.0
#     assert not task.ready
#     assert task.wait(timeout=1000) is True


# def test_poll_resource_poll_throttled():
#     """Assert calls to poll are throttled by the polling interval."""
#     task = PollResourceTask(StateMachine(), lambda a, headers: a, 0.001)
#     assert task._poll_count == 1

#     time.sleep(0.0011)  # TODO: not ideal to sleep in a test.
#     assert task._poll_count == 2

#     time.sleep(0.0021)  # TODO: not ideal to sleep in a test.
#     assert task._poll_count == 4


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
