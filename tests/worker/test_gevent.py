import gevent
import logging

from sentry_sdk.worker.gevent_worker import GeventWorker

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


SKIP_REASON = "gevent missing or broken"


def test_start():
    worker = GeventWorker()
    worker.start()
    assert worker.is_alive
    assert worker._greenlet is not None
    assert not worker._greenlet.dead
    worker.kill()


def test_kill():
    worker = GeventWorker()
    worker.start()

    greenlet = worker._greenlet
    worker.kill()
    assert not worker.is_alive
    assert worker._greenlet is None
    assert greenlet.dead


def test_kill_stalling_task(sentry_init, caplog):
    sentry_init(debug=True)
    worker = GeventWorker()

    task = mock.Mock(side_effect=lambda: gevent.sleep(2))
    worker.submit(task)
    assert worker._queue.qsize() == 1

    gevent.sleep(0)
    assert task.called

    worker.kill()
    assert not worker.is_alive
    assert worker._greenlet is None
    assert "gevent worker failed to terminate" in caplog.text


def test_submit(sentry_init, caplog):
    sentry_init(debug=True)
    worker = GeventWorker(queue_size=1)
    assert worker._queue.qsize() == 0
    task_1 = mock.Mock()

    worker.submit(task_1)
    assert worker._queue.qsize() == 1

    with caplog.at_level(logging.DEBUG):
        task_2 = mock.Mock()
        worker.submit(task_2)
        assert worker._queue.qsize() == 1
        assert "gevent worker queue full" in caplog.text

    worker.kill()


def test_flush():
    worker = GeventWorker()

    task_1 = mock.Mock()
    worker.submit(task_1)

    task_2 = mock.Mock()
    worker.submit(task_2)
    assert worker._queue.qsize() == 2
    assert not task_1.called
    assert not task_2.called

    worker.flush(0.01)
    assert worker._queue.qsize() == 0
    assert task_1.called
    assert task_2.called

    worker.kill()
