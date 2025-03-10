import asyncio
from datetime import timedelta

import pytest

from sentry_sdk import get_client, start_transaction
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


@pytest.fixture
def init_arq_with_dict_settings(sentry_init):
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
        )

        server = FakeRedis()
        pool = ArqRedis(pool_or_conn=server.connection_pool)

        worker_settings = {
            "functions": cls_functions,
            "cron_jobs": cls_cron_jobs,
            "redis_pool": pool,
            "allow_abort_jobs": allow_abort_jobs_,
        }

        if not worker_settings["functions"]:
            del worker_settings["functions"]
        if not worker_settings["cron_jobs"]:
            del worker_settings["cron_jobs"]

        worker = arq.worker.create_worker(worker_settings, **kwargs)

        return pool, worker

    return inner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
async def test_job_result(init_arq_settings, request):
    async def increase(ctx, num):
        return num + 1

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    increase.__qualname__ = increase.__name__

    pool, worker = init_fixture_method([increase])

    job = await pool.enqueue_job("increase", 3)

    assert isinstance(job, Job)

    await worker.run_job(job.job_id, timestamp_ms())
    result = await job.result()
    job_result = await job.result_info()

    assert result == 4
    assert job_result.result == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
async def test_job_retry(capture_events, init_arq_settings, request):
    async def retry_job(ctx):
        if ctx["job_try"] < 2:
            raise arq.worker.Retry

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    retry_job.__qualname__ = retry_job.__name__

    pool, worker = init_fixture_method([retry_job])

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
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
@pytest.mark.asyncio
async def test_job_transaction(
    capture_events, init_arq_settings, source, job_fails, request
):
    async def division(_, a, b=0):
        return a / b

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    division.__qualname__ = division.__name__

    cron_func = async_partial(division, a=1, b=int(not job_fails))
    cron_func.__qualname__ = division.__name__

    cron_job = cron(cron_func, minute=0, run_at_startup=True)

    functions_key, cron_jobs_key = source
    pool, worker = init_fixture_method(
        **{functions_key: [division], cron_jobs_key: [cron_job]}
    )

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
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
@pytest.mark.asyncio
async def test_enqueue_job(capture_events, init_arq_settings, source, request):
    async def dummy_job(_):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    pool, _ = init_fixture_method(**{source: [dummy_job]})

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
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
async def test_execute_job_without_integration(init_arq_settings, request):
    async def dummy_job(_ctx):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    dummy_job.__qualname__ = dummy_job.__name__

    pool, worker = init_fixture_method([dummy_job])
    # remove the integration to trigger the edge case
    get_client().integrations.pop("arq")

    job = await pool.enqueue_job("dummy_job")

    await worker.run_job(job.job_id, timestamp_ms())

    assert await job.result() is None


@pytest.mark.parametrize("source", ["cls_functions", "kw_functions"])
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
@pytest.mark.asyncio
async def test_span_origin_producer(capture_events, init_arq_settings, source, request):
    async def dummy_job(_):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    pool, _ = init_fixture_method(**{source: [dummy_job]})

    events = capture_events()

    with start_transaction():
        await pool.enqueue_job("dummy_job")

    (event,) = events
    assert event["contexts"]["trace"]["origin"] == "manual"
    assert event["spans"][0]["origin"] == "auto.queue.arq"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_arq_settings", ["init_arq", "init_arq_with_dict_settings"]
)
async def test_span_origin_consumer(capture_events, init_arq_settings, request):
    async def job(ctx):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    job.__qualname__ = job.__name__

    pool, worker = init_fixture_method([job])

    job = await pool.enqueue_job("retry_job")

    events = capture_events()

    await worker.run_job(job.job_id, timestamp_ms())

    (event,) = events

    assert event["contexts"]["trace"]["origin"] == "auto.queue.arq"
    assert event["spans"][0]["origin"] == "auto.db.redis"
    assert event["spans"][1]["origin"] == "auto.db.redis"


@pytest.mark.asyncio
async def test_job_concurrency(capture_events, init_arq):
    """
    10 - division starts
    70 - sleepy starts
    110 - division raises error
    120 - sleepy finishes

    """

    async def sleepy(_):
        await asyncio.sleep(0.05)

    async def division(_):
        await asyncio.sleep(0.1)
        return 1 / 0

    sleepy.__qualname__ = sleepy.__name__
    division.__qualname__ = division.__name__

    pool, worker = init_arq([sleepy, division])

    events = capture_events()

    await pool.enqueue_job(
        "division", _job_id="123", _defer_by=timedelta(milliseconds=10)
    )
    await pool.enqueue_job(
        "sleepy", _job_id="456", _defer_by=timedelta(milliseconds=70)
    )

    loop = asyncio.get_event_loop()
    task = loop.create_task(worker.async_run())
    await asyncio.sleep(1)

    task.cancel()

    await worker.close()

    exception_event = events[1]
    assert exception_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
    assert exception_event["transaction"] == "division"
    assert exception_event["extra"]["arq-job"]["task"] == "division"
