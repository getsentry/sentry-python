from unittest import mock

import pytest
import rq
from fakeredis import FakeRedis

import sentry_sdk
from sentry_sdk.consts import SPANDATA
from sentry_sdk.integrations.rq import RqIntegration
from sentry_sdk.utils import SENSITIVE_DATA_SUBSTITUTE, parse_version
from tests.integrations.utils import DATA_COLLECTION_QUEUES_CASES


@pytest.fixture(autouse=True)
def _patch_rq_get_server_version(monkeypatch):
    """
    Patch RQ lower than 1.5.1 to work with fakeredis.

    https://github.com/jamesls/fakeredis/issues/273
    """
    try:
        from distutils.version import StrictVersion
    except ImportError:
        return

    if parse_version(rq.VERSION) <= (1, 5, 1):
        for k in (
            "rq.job.Job.get_redis_server_version",
            "rq.worker.Worker.get_redis_server_version",
        ):
            try:
                monkeypatch.setattr(k, lambda _: StrictVersion("4.0.0"))
            except AttributeError:
                # old RQ Job/Worker doesn't have a get_redis_server_version attr
                pass


def crashing_job(foo, b=1):
    1 / 0


def chew_up_shoes(dog, human, shoes):
    raise Exception("{}!! Why did you eat {}'s {}??".format(dog, human, shoes))


def do_trick(dog, trick):
    return "{}, can you {}? Good dog!".format(dog, trick)


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_basic(
    sentry_init,
    capture_items,
    send_default_pii,
):
    sentry_init(
        integrations=[RqIntegration()],
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event")

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    (event,) = (item.payload for item in items)

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "rq"
    assert exception["stacktrace"]["frames"][-1]["vars"]["foo"] == "42"

    extra = event["extra"]["rq-job"]
    if send_default_pii:
        assert extra["args"] == []
        assert extra["kwargs"] == {"foo": 42}
    else:
        assert extra["args"] == SENSITIVE_DATA_SUBSTITUTE
        assert extra["kwargs"] == SENSITIVE_DATA_SUBSTITUTE
    assert extra["description"] == "tests.integrations.rq.test_rq.crashing_job(foo=42)"
    assert extra["func"] == "tests.integrations.rq.test_rq.crashing_job"
    assert "job_id" in extra
    assert "enqueued_at" in extra

    # older versions don't persist started_at correctly
    if tuple(map(int, rq.VERSION.split("."))) >= (0, 9):
        assert "started_at" in extra


@pytest.mark.parametrize(
    "init_kwargs,expected_args,expected_kwargs",
    DATA_COLLECTION_QUEUES_CASES,
)
def test_job_args_kwargs_data_collection(
    sentry_init,
    capture_items,
    init_kwargs,
    expected_args,
    expected_kwargs,
):
    sentry_init(
        integrations=[RqIntegration()],
        trace_lifecycle="stream",
        **init_kwargs,
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event")

    queue.enqueue(crashing_job, 1, b=0)
    worker.work(burst=True)

    (event,) = (item.payload for item in items)

    rq_job = event["extra"]["rq-job"]

    if expected_args is None:
        assert "args" not in rq_job
        assert "kwargs" not in rq_job
    else:
        assert rq_job["args"] == expected_args
        assert rq_job["kwargs"] == expected_kwargs


def test_transport_shutdown(
    sentry_init,
    capture_items_forksafe,
):
    sentry_init(
        integrations=[RqIntegration()],
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.Worker([queue], connection=queue.connection)
    items = capture_items_forksafe("event")

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    captured_items = items.read_event()
    items.read_flush()

    event = next(item["payload"] for item in captured_items)
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_worker_span_with_error(
    sentry_init,
    capture_items,
    send_default_pii,
):
    sentry_init(
        integrations=[RqIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event", "span")

    queue.enqueue(chew_up_shoes, "Charlie", "Katie", shoes="flip-flops")
    worker.work(burst=True)

    (error_event,) = (item.payload for item in items if item.type == "event")

    assert error_event["transaction"] == "tests.integrations.rq.test_rq.chew_up_shoes"
    assert error_event["contexts"]["trace"]["op"] == "queue.task.rq"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert (
        error_event["exception"]["values"][0]["value"]
        == "Charlie!! Why did you eat Katie's flip-flops??"
    )

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = (
        span for span in spans if span["attributes"].get("sentry.op") == "queue.task.rq"
    )

    assert span["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
    assert span["span_id"] == error_event["contexts"]["trace"]["span_id"]
    assert span["attributes"]["sentry.op"] == error_event["contexts"]["trace"]["op"]
    assert (
        span["attributes"]["sentry.origin"]
        == error_event["contexts"]["trace"]["origin"]
    )
    assert (
        span["attributes"][SPANDATA.CODE_FUNCTION_NAME]
        == "tests.integrations.rq.test_rq.chew_up_shoes"
    )


def test_error_has_trace_context_if_tracing_disabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[RqIntegration()],
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event")

    queue.enqueue(crashing_job, foo=None)
    worker.work(burst=True)

    (error_event,) = (item.payload for item in items)

    assert error_event["contexts"]["trace"]


def test_tracing_enabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[RqIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event", "span")

    with sentry_sdk.traces.start_span(
        name="custom parent",
        attributes={
            "sentry.op": "rq transaction",
        },
    ) as span:
        queue.enqueue(crashing_job, foo=None)
        worker.work(burst=True)

    (error_event,) = (item.payload for item in items if item.type == "event")

    assert error_event["contexts"]["trace"]["trace_id"] == span.trace_id

    sentry_sdk.flush()
    spans = [item.payload for item in items if item.type == "span"]
    (span,) = (
        span for span in spans if span["attributes"].get("sentry.op") == "queue.task.rq"
    )

    assert span["trace_id"] == error_event["contexts"]["trace"]["trace_id"]
    assert span["span_id"] == error_event["contexts"]["trace"]["span_id"]
    assert span["attributes"]["sentry.op"] == error_event["contexts"]["trace"]["op"]
    assert (
        span["attributes"]["sentry.origin"]
        == error_event["contexts"]["trace"]["origin"]
    )


def test_tracing_disabled(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[RqIntegration()],
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event")

    queue.enqueue(crashing_job, foo=None)
    worker.work(burst=True)

    (error_event,) = (item.payload for item in items)

    assert error_event["contexts"]["trace"]["trace_id"]


@pytest.mark.parametrize("send_default_pii", [True, False])
def test_worker_span_no_error(
    sentry_init,
    capture_items,
    send_default_pii,
):
    sentry_init(
        integrations=[RqIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=send_default_pii,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("span")

    queue.enqueue(do_trick, "Maisey", trick="kangaroo")
    worker.work(burst=True)

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = (
        span for span in spans if span["attributes"].get("sentry.op") == "queue.task.rq"
    )

    assert span["attributes"]["sentry.op"] == "queue.task.rq"
    assert span["name"] == "tests.integrations.rq.test_rq.do_trick"
    assert span["attributes"][SPANDATA.MESSAGING_DESTINATION_NAME] == queue.name


def test_traces_sampler_gets_correct_values_in_sampling_context(
    sentry_init,
    DictionaryContaining,
    ObjectDescribedBy,
):
    traces_sampler = mock.Mock(return_value=True)
    sentry_init(
        integrations=[RqIntegration()],
        traces_sampler=traces_sampler,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(do_trick, "Bodhi", trick="roll over")
    worker.work(burst=True)

    traces_sampler.assert_any_call(
        DictionaryContaining(
            {
                "rq_job": ObjectDescribedBy(
                    type=rq.job.Job,
                    attrs={
                        "description": "tests.integrations.rq.test_rq.do_trick('Bodhi', trick='roll over')",
                        "result": "Bodhi, can you roll over? Good dog!",
                        "func_name": "tests.integrations.rq.test_rq.do_trick",
                        "args": ("Bodhi",),
                        "kwargs": {"trick": "roll over"},
                    },
                ),
            }
        )
    )


@pytest.mark.skipif(
    parse_version(rq.__version__) < (1, 5), reason="At least rq-1.5 required"
)
def test_job_with_retries(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[RqIntegration()],
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("event")

    queue.enqueue(crashing_job, foo=42, retry=rq.Retry(max=1))
    worker.work(burst=True)
    events = [item.payload for item in items]

    assert len(events) == 1


def test_span_origin(
    sentry_init,
    capture_items,
):
    sentry_init(
        integrations=[RqIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )

    queue = rq.Queue(connection=FakeRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)
    items = capture_items("span")

    queue.enqueue(do_trick, "Maisey", trick="kangaroo")
    worker.work(burst=True)

    sentry_sdk.flush()
    spans = [item.payload for item in items]
    (span,) = (
        span for span in spans if span["attributes"].get("sentry.op") == "queue.task.rq"
    )

    assert span["attributes"]["sentry.origin"] == "auto.queue.rq"
