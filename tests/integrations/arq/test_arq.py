import asyncio
from datetime import timedelta

import arq.worker
import pytest
from arq import cron
from arq.connections import ArqRedis
from arq.jobs import Job
from arq.utils import timestamp_ms
from fakeredis.aioredis import FakeRedis

import sentry_sdk
from sentry_sdk import get_client, start_transaction
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.arq import ArqIntegration


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
        span_streaming,
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
            _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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
        span_streaming,
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
            _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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


@pytest.fixture
def init_arq_with_kwarg_settings(sentry_init):
    """Test fixture that passes settings_cls as keyword argument only."""

    def inner(
        span_streaming,
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
            _experiments={"trace_lifecycle": "stream" if span_streaming else "static"},
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

        # Pass settings_cls as keyword argument (not positional)
        worker = arq.worker.create_worker(settings_cls=WorkerSettings, **kwargs)

        return pool, worker

    return inner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "init_arq_settings",
    ["init_arq", "init_arq_with_dict_settings", "init_arq_with_kwarg_settings"],
)
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_job_result(init_arq_settings, request, span_streaming):
    async def increase(ctx, num):
        return num + 1

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    increase.__qualname__ = increase.__name__

    pool, worker = init_fixture_method(span_streaming, [increase])

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_job_retry(
    capture_events,
    capture_items,
    init_arq_settings,
    request,
    span_streaming,
):
    async def retry_job(ctx):
        if ctx["job_try"] < 2:
            raise arq.worker.Retry

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    retry_job.__qualname__ = retry_job.__name__

    pool, worker = init_fixture_method(span_streaming, [retry_job])

    job = await pool.enqueue_job("retry_job")

    if span_streaming:
        items = capture_items("span")

        await worker.run_job(job.job_id, timestamp_ms())

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # The retry re-enqueue happens without an active span, so no producer
        # (queue.submit.arq) span is created for it; only the consumer segment
        # is emitted. The consumer segment is preceded by the redis spans for
        # the re-enqueue, so it lands at index 2.
        assert spans[2]["attributes"]["sentry.op"] == "queue.task.arq"
        assert spans[2]["status"] == "ok"
        assert spans[2]["name"] == "retry_job"

        await worker.run_job(job.job_id, timestamp_ms())

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[5]["attributes"]["sentry.op"] == "queue.task.arq"
        assert spans[5]["status"] == "ok"
        assert spans[5]["name"] == "retry_job"
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_job_transaction(
    capture_events,
    capture_items,
    init_arq_settings,
    source,
    job_fails,
    request,
    span_streaming,
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
        span_streaming, **{functions_key: [division], cron_jobs_key: [cron_job]}
    )

    job = await pool.enqueue_job("division", 1, b=int(not job_fails))

    if span_streaming:
        items = capture_items("event", "span")

        await worker.run_job(job.job_id, timestamp_ms())

        loop = asyncio.get_event_loop()
        task = loop.create_task(worker.async_run())
        await asyncio.sleep(1)

        task.cancel()

        await worker.close()

        events = [item.payload for item in items if item.type == "event"]
        if job_fails:
            error_func_event = events.pop(0)
            error_cron_event = events.pop(0)

            assert (
                error_func_event["exception"]["values"][0]["type"]
                == "ZeroDivisionError"
            )
            assert (
                error_func_event["exception"]["values"][0]["mechanism"]["type"] == "arq"
            )

            func_extra = error_func_event["extra"]["arq-job"]
            assert func_extra["task"] == "division"

            assert (
                error_cron_event["exception"]["values"][0]["type"]
                == "ZeroDivisionError"
            )
            assert (
                error_cron_event["exception"]["values"][0]["mechanism"]["type"] == "arq"
            )

            cron_extra = error_cron_event["extra"]["arq-job"]
            assert cron_extra["task"] == "cron:division"

        sentry_sdk.flush()
        spans = [item.payload for item in items if item.type == "span"]

        task_spans = [
            span
            for span in spans
            if span["attributes"].get("sentry.op") == "queue.task.arq"
        ]

        division_span = next(span for span in task_spans if span["name"] == "division")
        assert division_span["attributes"]["sentry.span.source"] == "task"
        assert (
            division_span["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME]
            == worker.queue_name
        )

        assert any(span["name"] == "cron:division" for span in task_spans)
    else:
        events = capture_events()

        await worker.run_job(job.job_id, timestamp_ms())

        loop = asyncio.get_event_loop()
        task = loop.create_task(worker.async_run())
        await asyncio.sleep(1)

        task.cancel()

        await worker.close()

        if job_fails:
            error_func_event = events.pop(0)
            error_cron_event = events.pop(1)

            assert (
                error_func_event["exception"]["values"][0]["type"]
                == "ZeroDivisionError"
            )
            assert (
                error_func_event["exception"]["values"][0]["mechanism"]["type"] == "arq"
            )

            func_extra = error_func_event["extra"]["arq-job"]
            assert func_extra["task"] == "division"

            assert (
                error_cron_event["exception"]["values"][0]["type"]
                == "ZeroDivisionError"
            )
            assert (
                error_cron_event["exception"]["values"][0]["mechanism"]["type"] == "arq"
            )

            cron_extra = error_cron_event["extra"]["arq-job"]
            assert cron_extra["task"] == "cron:division"

        [func_event, cron_event] = events

        assert func_event["type"] == "transaction"
        assert func_event["transaction"] == "division"
        assert func_event["transaction_info"] == {"source": "task"}
        assert (
            func_event["contexts"]["trace"]["data"][SPANDATA.MESSAGING_DESTINATION_NAME]
            == worker.queue_name
        )

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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_enqueue_job(
    capture_events,
    capture_items,
    init_arq_settings,
    source,
    request,
    span_streaming,
):
    async def dummy_job(_):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    pool, _ = init_fixture_method(span_streaming, **{source: [dummy_job]})

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent") as span:
            await pool.enqueue_job("dummy_job")

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        assert spans[2]["is_segment"] is True
        assert spans[2]["trace_id"] == span.trace_id
        assert spans[2]["span_id"] == span.span_id

        assert spans[1]["attributes"]["sentry.op"] == "queue.submit.arq"
        assert spans[1]["name"] == "dummy_job"
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_execute_job_without_integration(
    init_arq_settings, request, span_streaming
):
    async def dummy_job(_ctx):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    dummy_job.__qualname__ = dummy_job.__name__

    pool, worker = init_fixture_method(span_streaming, [dummy_job])
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_span_origin_producer(
    capture_events,
    capture_items,
    init_arq_settings,
    source,
    request,
    span_streaming,
):
    async def dummy_job(_):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    pool, _ = init_fixture_method(span_streaming, **{source: [dummy_job]})

    if span_streaming:
        items = capture_items("span")

        with sentry_sdk.traces.start_span(name="custom parent"):
            await pool.enqueue_job("dummy_job")

        sentry_sdk.flush()
        spans = [item.payload for item in items]
        assert spans[2]["attributes"]["sentry.origin"] == "manual"
        assert spans[1]["attributes"]["sentry.origin"] == "auto.queue.arq"
    else:
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
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_span_origin_consumer(
    capture_events,
    capture_items,
    init_arq_settings,
    request,
    span_streaming,
):
    async def job(ctx):
        pass

    init_fixture_method = request.getfixturevalue(init_arq_settings)

    job.__qualname__ = job.__name__

    pool, worker = init_fixture_method(span_streaming, [job])

    if span_streaming:
        job = await pool.enqueue_job("job")

        items = capture_items("span")

        await worker.run_job(job.job_id, timestamp_ms())

        sentry_sdk.flush()
        spans = [item.payload for item in items]

        # No producer (queue.submit.arq) span is created for the re-enqueue
        # triggered by the retry, since it happens without an active span, so
        # the consumer segment lands at index 2.
        assert spans[2]["attributes"]["sentry.op"] == "queue.task.arq"
        assert spans[2]["attributes"]["sentry.origin"] == "auto.queue.arq"
        assert spans[1]["attributes"]["sentry.origin"] == "auto.db.redis"
        assert spans[0]["attributes"]["sentry.origin"] == "auto.db.redis"
    else:
        job = await pool.enqueue_job("job")

        events = capture_events()

        await worker.run_job(job.job_id, timestamp_ms())

        (event,) = events

        assert event["contexts"]["trace"]["origin"] == "auto.queue.arq"
        assert event["spans"][0]["origin"] == "auto.db.redis"
        assert event["spans"][1]["origin"] == "auto.db.redis"


@pytest.mark.asyncio
@pytest.mark.parametrize("span_streaming", [True, False])
async def test_job_concurrency(
    capture_events,
    capture_items,
    init_arq,
    span_streaming,
):
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

    pool, worker = init_arq(span_streaming, [sleepy, division])

    await pool.enqueue_job(
        "division", _job_id="123", _defer_by=timedelta(milliseconds=10)
    )
    await pool.enqueue_job(
        "sleepy", _job_id="456", _defer_by=timedelta(milliseconds=70)
    )

    if span_streaming:
        items = capture_items("event")

        loop = asyncio.get_event_loop()
        task = loop.create_task(worker.async_run())
        await asyncio.sleep(1)

        task.cancel()

        await worker.close()

        events = [item.payload for item in items]
        exception_event = events[0]
        assert exception_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert exception_event["transaction"] == "division"
    else:
        events = capture_events()

        loop = asyncio.get_event_loop()
        task = loop.create_task(worker.async_run())
        await asyncio.sleep(1)

        task.cancel()

        await worker.close()

        exception_event = events[1]
        assert exception_event["exception"]["values"][0]["type"] == "ZeroDivisionError"
        assert exception_event["transaction"] == "division"

    assert exception_event["extra"]["arq-job"]["task"] == "division"
