# -*- codong: utf-8 -*-
from abc import abstractmethod
import asyncio
from datetime import datetime, timedelta
import gzip
import io
import json

from aiohttp import ClientSession
from sentry_sdk import Transport, VERSION
from sentry_sdk.utils import logger, capture_internal_exceptions
import urllib3


class AioHttpTransportBase(Transport):

  def __init__(self, options):
    Transport.__init__(self, options)
    self._auth = self.parsed_dsn.to_auth("sentry-python/%s" % VERSION)
    self._loop = None
    self._session = None
    self._disabled_until = None
    self.options = options
    self._retry = urllib3.util.Retry()
    self._disabled_until = None

    from sentry_sdk import Hub

    self.hub_cls = Hub

  @property
  def is_closed(self):
    return not self._session or self._session.closed

  def get_loop(self):
    loop = self._loop
    if not loop:
      self._loop = loop = asyncio.get_event_loop()
    return loop

  def get_sess(self):
    sess = self._session
    if not sess:
      headers = {
        'X-Sentry-Auth': str(self._auth.to_header()),
        'Content-Type': 'application/json', 'Content-Encoding': 'gzip', }
      self._session = sess = ClientSession(headers=headers, loop=self.get_loop())
    return sess

  def capture_event(self, event):
    self._do_send_event(event)

  @abstractmethod
  def _do_send_event(self, event):
    pass

  async def _send_event(self, event):
    if self._disabled_until is not None:
      if datetime.utcnow() < self._disabled_until:
        return
      self._disabled_until = None

    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode='w') as f:
      f.write(json.dumps(event).encode('utf-8'))

    logger.debug(
      "Sending %s event [%s] to %s project:%s" % (
        event.get("level") or "error", event["event_id"], self.parsed_dsn.host,
        self.parsed_dsn.project_id,))

    session = self.get_sess()
    response = await session.request(
      'POST', str(self._auth.store_api_url), data=body.getvalue(), )
    if response.status == 429:
      self._disabled_until = datetime.utcnow() + timedelta(
        seconds=self._retry.get_retry_after(response) or 60)
    elif response.status >= 300 or response.status < 200:
      raise ValueError("Unexpected status code: %s" % response.status)

  @abstractmethod
  async def _close(self, timeout):
    pass

  async def close(self, timeout):
    await self._close(timeout)

    session = self._session
    if session and not session.closed:
      await session.close()

  def shutdown(self, timeout, callback=None):
    logger.debug("Shutting down HTTP transport orderly")

    loop = self._loop
    if loop and not loop.is_closed():
      loop.create_task(self.close(timeout=timeout))

  def kill(self):
    logger.debug("Killing HTTP transport")
    if self.is_closed:
      return
    loop = self._loop
    if loop and not loop.is_closed():
      loop.create_task(self.close(0))

  def copy(self):
    transport = type(self)(self.options)
    transport._session = self._session
    return transport


class AioHttpTransport(AioHttpTransportBase):
  """The default HTTP transport."""

  def __init__(self, options):
    super().__init__(options)
    self._tasks = set()

  def _do_send_event(self, event):
    hub = self.hub_cls.current

    async def send_event_wrapper(hub):
      with hub:
        with capture_internal_exceptions():
          await self._send_event(event)

    # self._worker.submit(send_event_wrapper)
    loop = self.get_loop()
    task:asyncio.Task = loop.create_task(send_event_wrapper(hub))
    self._tasks.add(task)
    task.add_done_callback(self._tasks.remove)

  async def _close(self, timeout):
    logger.debug("Close down HTTP transport orderly")
    loop = self._loop
    tasks = tuple(t for t in self._tasks if not t.done())
    if tasks:
      if timeout == 0:
        for t in tasks:
          t.cancel()
      await asyncio.wait(tasks, timeout=timeout, loop=loop)


class QueuedAioHttpTransport(AioHttpTransportBase):
  def __init__(self, options):
    super().__init__(options)
    self.options = options

    loop = self.get_loop()

    qsize = options.get('transport_qsize', 1000)
    self._queue = asyncio.Queue(maxsize=qsize, loop=loop)

    cnt_workers = options.get('transport_workers', 10)
    self._workers = workers = set()

    for _ in range(cnt_workers):
      worker = loop.create_task(self._worker())
      workers.add(worker)
      worker.add_done_callback(workers.remove)

  async def _worker(self):
    while True:
      event = await self._queue.get()

      try:
        if event is ...:
          self._queue.put_nowait(...)
          return

        await self._send_event(event)
      finally:
        self._queue.task_done()

  def _do_send_event(self, event):
    try:
      self._queue.put_nowait(event)
    except asyncio.QueueFull as exc:
      skipped = self._queue.get_nowait()
      self._queue.task_done()

      logger.warning('QueuedAioHttpTransport internal queue is full')
      self._queue.put_nowait(event)

  async def _close(self, timeout):
    try:
      self._queue.put_nowait(...)
    except asyncio.QueueFull as exc:
      skipped = self._queue.get_nowait()
      self._queue.task_done()

      logger.warning('QueuedAioHttpTransport internal queue is full')

      self._queue.put_nowait(...)

    await asyncio.gather(*self._workers, return_exceptions=True,
      loop=self._loop)

    assert len(self._workers) == 0
    assert self._queue.qsize() == 1
    try:
      assert self._queue.get_nowait() is ...
    finally:
      self._queue.task_done()
