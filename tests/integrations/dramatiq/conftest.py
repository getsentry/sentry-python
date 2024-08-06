import pytest

import dramatiq
from dramatiq.brokers.stub import StubBroker

from sentry_sdk.integrations.dramatiq import DramatiqIntegration


@pytest.fixture
def broker(sentry_init):
    sentry_init(integrations=[DramatiqIntegration()])
    broker = StubBroker()
    broker.emit_after("process_boot")
    dramatiq.set_broker(broker)
    yield broker
    broker.flush_all()
    broker.close()


@pytest.fixture
def worker(broker):
    worker = dramatiq.Worker(broker, worker_timeout=100, worker_threads=1)
    worker.start()
    yield worker
    worker.stop()
