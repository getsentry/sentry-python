# isort:skip_file
import pytest

pytest.importorskip("dramatiq")

import dramatiq
from dramatiq.brokers.stub import StubBroker

import sentry_sdk
from sentry_sdk.transport import Transport
from sentry_sdk.integrations.dramatiq import DramatiqIntegration


# We need to use this instead of the sentry_init fixture because
# Dramatiq's stub worker executes tasks in a background thread.
class _TestTransport(Transport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.captured_events = []

    def capture_event(self, event):
        self.captured_events.append(event)


@pytest.fixture
def transport():
    return _TestTransport()


@pytest.fixture
def broker(transport):
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    sentry_sdk.init(transport=transport, integrations=[DramatiqIntegration()])
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def worker(broker):
    worker = dramatiq.Worker(broker, worker_timeout=100, worker_threads=1)
    worker.start()
    yield worker
    worker.stop()


def test_simple(transport, broker, worker):
    @dramatiq.actor(max_retries=0)
    def dummy_actor(x, y):
        foo = 42  # noqa
        return x / y

    dummy_actor.send(1, 2)
    dummy_actor.send(1, 0)
    broker.join(dummy_actor.queue_name)
    worker.join()

    event = transport.captured_events[0]
    assert event["transaction"] == "dummy_actor"

    exception = event["exception"]["values"][0]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "dramatiq"
    assert exception["stacktrace"]["frames"][-1]["vars"] == {
        "x": "1",
        "y": "0",
        "foo": "42",
    }
