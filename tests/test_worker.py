import threading

import time

from sentry_sdk.worker import BackgroundWorker


def test_flush_starvation(request):
    worker = BackgroundWorker()
    request.addfinalizer(worker.kill)

    def send():
        time.sleep(1)

    worker.submit(send)

    def spam_queue():
        for _ in range(10):
            time.sleep(0.1)
            worker.submit(send)

    t = threading.Thread(target=spam_queue)
    t.daemon = True
    t.start()

    before = time.time()
    worker.flush(10)
    after = time.time()
    duration = after - before

    assert duration < 2
