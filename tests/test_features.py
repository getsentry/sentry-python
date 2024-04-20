from sentry_sdk.features import PollResourceTask
import time


class HTTPResponse:

    def __init__(self, status_code, headers, content):
        self.status_code = status_code
        self.headers = headers
        self.content = content

    def __getitem__(self, key):
        return self.headers[key]

    def json(self):
        return self.content


class StateMachine:
    def __init__(self):
        self.messages = []

    def update(self, message):
        self.messages.append(message)


# Test PollResourceTask


def test_poll_resource_task_failure():
    """Assert on failure response the poll task marks itself as ready and waits."""

    def failure_response(callback, headers):
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(), failure_response, 30.0)
    assert task.ready
    assert not task.closed
    assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


def test_poll_resource_task_success():
    """Assert on success response the poll task marks itself as ready and waits."""

    def success_response(callback, headers):
        callback(HTTPResponse(200, {"ETag": "hello"}, {"version": 1, "features": []}))

    task = PollResourceTask(StateMachine(), success_response, 30.0)
    assert task.ready
    assert not task.closed
    assert int(task.next_poll_time - time.time() - 30) == 0, task.next_poll_time


def test_poll_resource_wait_faster():
    """Assert responses faster than the blocking wait return true."""

    def fast_response(callback, headers):
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(), fast_response, 30.0)
    assert task.wait(timeout=0.001) is True


def test_poll_resource_wait_slower():
    """Assert responses slower than the blocking wait return false."""

    def slow_response(callback, headers):
        time.sleep(0.001)
        callback(HTTPResponse(400, {"ETag": "hello"}, ""))

    task = PollResourceTask(StateMachine(), slow_response, 30.0)
    assert task.wait(timeout=0) is False


def test_poll_resource_close():
    """Assert a closed poll resource can no longer poll."""
    task = PollResourceTask(
        StateMachine(),
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
