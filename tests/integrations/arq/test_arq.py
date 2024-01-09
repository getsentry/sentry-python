import asyncio
import pytest

from sentry_sdk import start_transaction, Hub
from sentry_sdk.integrations.arq import ArqIntegration

import arq.worker
from arq import cron
from arq.connections import ArqRedis
from arq.jobs import Job
from arq.utils import timestamp_ms

from fakeredis.aioredis import FakeRedis


def async_partial(async_fn, *args, **kwargs):
    # asyncio.iscoroutinefunction (Used in the integration code) in Python < 3.8
    # does not detect async functions in functools.partial objects.
    # This partial implementation returns a coroutine instead.
    async def wrapped(ctx):
        return await async_fn(ctx, *args, **kwargs)

    return wrapped


@pytest.fixture(autouse=True)
def patch_fakeredis_info_command():
    from fakeredis._fakesocket import FakeSocket

    if not hasattr(FakeSocket, "info"):
        from fakeredis._commands import command
        from fakeredis._helpers import SimpleString

        @command((SimpleString,), name="info")
        def info(self, section):
            return section

        FakeSocket.info = info


@pytest.fixture
def init_arq(sentry_init):
    def inner(
        cls_functions=None,
        cls_cron_jobs=None,
        kw_functions=None,
        kw_cron_jobs=None,
        allow_abort_jobs_=False,
    ):
        cls_functions = cls_functions or []
        cls_cron_jobs = cls_cron_jobs or []

        kwargs = {}
        if kw_functions is not None:
            kwargs["functions"] = kw_functions
        if kw_cron_jobs is not None:
            kwargs["cron_jobs"] = kw_cron_jobs

        sentry_init(
            integrations=[ArqIntegration()],
            traces_sample_rate=1.0,
            send_default_pii=True,
            debug=True,
        )

        server = FakeRedis()
        pool = ArqRedis(pool_or_conn=server.connection_pool)

        class WorkerSettings:
            functions = cls_functions
            cron_jobs = cls_cron_jobs
            redis_pool = pool
            allow_abort_jobs = allow_abort_jobs_

        if not WorkerSettings.functions:
            del WorkerSettings.functions
        if not WorkerSettings.cron_jobs:
            del WorkerSettings.cron_jobs

        worker = arq.worker.create_worker(WorkerSettings, **kwargs)

        return pool, worker

    return inner


@pytest.mark.asyncio
async def test_job_result(init_arq):
    async def increase(ctx, num):
        return num + 1

    increase.__qualname__ = increase.__name__

    pool, worker = init_arq([increase])

    job = await pool.enqueue_job("increase", 3)

    assert isinstance(job, Job)

    await worker.run_job(job.job_id, timestamp_ms())
    result = await job.result()
    job_result = await job.result_info()

    assert result == 4
    assert job_result.result == 4


@pytest.mark.asyncio
async def test_job_retry(capture_events, init_arq):
    async def retry_job(ctx):
        if ctx["job_try"] < 2:
            raise arq.worker.Retry

    retry_job.__qualname__ = retry_job.__name__

    pool, worker = init_arq([retry_job])

    job = await pool.enqueue_job("retry_job")

    events = capture_events()

    await worker.run_job(job.job_id, timestamp_ms())

    event = events.pop(0)
    assert event["contexts"]["trace"]["status"] == "aborted"
    assert event["transaction"] == "retry_job"
    assert event["tags"]["arq_task_id"] == job.job_id
    assert event["extra"]["arq-job"]["retry"] == 1

    await worker.run_job(job.job_id, timestamp_ms())

    event = events.pop(0)
    assert event["contexts"]["trace"]["status"] == "ok"
    assert event["transaction"] == "retry_job"
    assert event["tags"]["arq_task_id"] == job.job_id
    assert event["extra"]["arq-job"]["retry"] == 2


@pytest.mark.parametrize(
    "source", [("cls_functions", "cls_cron_jobs"), ("kw_functions", "kw_cron_jobs")]
)
@pytest.mark.parametrize("job_fails", [True, False], ids=["error", "success"])
@pytest.mark.asyncio
async def test_job_transaction(capture_events, init_arq, source, job_fails):
    async def division(_, a, b=0):
        return a / b

    division.__qualname__ = division.__name__

    cron_func = async_partial(division, a=1, b=int(not job_fails))
    cron_func.__qualname__ = division.__name__

    cron_job = cron(cron_func, minute=0, run_at_startup=True)

    functions_key, cron_jobs_key = source
    pool, worker = init_arq(**{functions_key: [division], cron_jobs_key: [cron_job]})

    events = capture_events()

    job = await pool.enqueue_job("division", 1, b=int(not job_fails))
    await worker.run_job(job.job_id, timestamp_ms())

    loop = asyncio.get_event_loop()
    task = loop.create_task(worker.async_run())
    await asyncio.sleep(1)

    task.cancel()

    await worker.close()

    if job_fails:
        error_func_event = events.pop(0)
        error_cron_event = events.pop(1)

        assert error_func_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert error_func_event["exception"]["values"][0]["mechanism"]["type"] == "arq"

        func_extra = error_func_event["extra"]["arq-job"]
        assert func_extra["task"] == "division"

        assert error_cron_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert error_cron_event["exception"]["values"][0]["mechanism"]["type"] == "arq"

        cron_extra = error_cron_event["extra"]["arq-job"]
        assert cron_extra["task"] == "cron:division"

    [func_event, cron_event] = events

    assert func_event["type"] == "transaction"
    assert func_event["transaction"] == "division"
    assert func_event["transaction_info"] == {"source": "task"}

    assert "arq_task_id" in func_event["tags"]
    assert "arq_task_retry" in func_event["tags"]

    func_extra = func_event["extra"]["arq-job"]

    assert func_extra["task"] == "division"
    assert func_extra["kwargs"] == {"b": int(not job_fails)}
    assert func_extra["retry"] == 1

    assert cron_event["type"] == "transaction"
    assert cron_event["transaction"] == "cron:division"
    assert cron_event["transaction_info"] == {"source": "task"}

    assert "arq_task_id" in cron_event["tags"]
    assert "arq_task_retry" in cron_event["tags"]

    cron_extra = cron_event["extra"]["arq-job"]

    assert cron_extra["task"] == "cron:division"
    assert cron_extra["kwargs"] == {}
    assert cron_extra["retry"] == 1


@pytest.mark.parametrize("source", ["cls_functions", "kw_functions"])
@pytest.mark.asyncio
async def test_enqueue_job(capture_events, init_arq, source):
    async def dummy_job(_):
        pass

    pool, _ = init_arq(**{source: [dummy_job]})

    events = capture_events()

    with start_transaction() as transaction:
        await pool.enqueue_job("dummy_job")

    (event,) = events

    assert event["contexts"]["trace"]["trace_id"] == transaction.trace_id
    assert event["contexts"]["trace"]["span_id"] == transaction.span_id

    assert len(event["spans"])
    assert event["spans"][0]["op"] == "queue.submit.arq"
    assert event["spans"][0]["description"] == "dummy_job"


@pytest.mark.asyncio
async def test_execute_job_without_integration(init_arq):
    async def dummy_job(_ctx):
        pass

    dummy_job.__qualname__ = dummy_job.__name__

    pool, worker = init_arq([dummy_job])
    # remove the integration to trigger the edge case
    Hub.current.client.integrations.pop("arq")

    job = await pool.enqueue_job("dummy_job")

    await worker.run_job(job.job_id, timestamp_ms())

    assert await job.result() is None
