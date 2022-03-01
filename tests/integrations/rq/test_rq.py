import pytest
from fakeredis import FakeStrictRedis
from sentry_sdk.integrations.rq import RqIntegration

import rq

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


@pytest.fixture(autouse=True)
def _patch_rq_get_server_version(monkeypatch):
    """
    Patch up RQ 1.5 to work with fakeredis.

    https://github.com/jamesls/fakeredis/issues/273
    """

    from distutils.version import StrictVersion

    if tuple(map(int, rq.VERSION.split("."))) >= (1, 5):
        for k in (
            "rq.job.Job.get_redis_server_version",
            "rq.worker.Worker.get_redis_server_version",
        ):
            monkeypatch.setattr(k, lambda _: StrictVersion("4.0.0"))


def crashing_job(foo):
    1 / 0


def chew_up_shoes(dog, human, shoes):
    raise Exception("{}!! Why did you eat {}'s {}??".format(dog, human, shoes))


def do_trick(dog, trick):
    return "{}, can you {}? Good dog!".format(dog, trick)


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[RqIntegration()])
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    (event,) = events

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"
    assert exception["mechanism"]["type"] == "rq"
    assert exception["stacktrace"]["frames"][-1]["vars"]["foo"] == "42"

    assert event["transaction"] == "tests.integrations.rq.test_rq.crashing_job"
    assert event["extra"]["rq-job"] == {
        "args": [],
        "description": "tests.integrations.rq.test_rq.crashing_job(foo=42)",
        "func": "tests.integrations.rq.test_rq.crashing_job",
        "job_id": event["extra"]["rq-job"]["job_id"],
        "kwargs": {"foo": 42},
    }


def test_transport_shutdown(sentry_init, capture_events_forksafe):
    sentry_init(integrations=[RqIntegration()])

    events = capture_events_forksafe()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.Worker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42)
    worker.work(burst=True)

    event = events.read_event()
    events.read_flush()

    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ZeroDivisionError"


def test_transaction_with_error(
    sentry_init, capture_events, DictionaryContaining  # noqa:N803
):

    sentry_init(integrations=[RqIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(chew_up_shoes, "Charlie", "Katie", shoes="flip-flops")
    worker.work(burst=True)

    error_event, envelope = events

    assert error_event["transaction"] == "tests.integrations.rq.test_rq.chew_up_shoes"
    assert error_event["contexts"]["trace"]["op"] == "rq.task"
    assert error_event["exception"]["values"][0]["type"] == "Exception"
    assert (
        error_event["exception"]["values"][0]["value"]
        == "Charlie!! Why did you eat Katie's flip-flops??"
    )

    assert envelope["type"] == "transaction"
    assert envelope["contexts"]["trace"] == error_event["contexts"]["trace"]
    assert envelope["transaction"] == error_event["transaction"]
    assert envelope["extra"]["rq-job"] == DictionaryContaining(
        {
            "args": ["Charlie", "Katie"],
            "kwargs": {"shoes": "flip-flops"},
            "func": "tests.integrations.rq.test_rq.chew_up_shoes",
            "description": "tests.integrations.rq.test_rq.chew_up_shoes('Charlie', 'Katie', shoes='flip-flops')",
        }
    )


def test_transaction_no_error(
    sentry_init, capture_events, DictionaryContaining  # noqa:N803
):
    sentry_init(integrations=[RqIntegration()], traces_sample_rate=1.0)
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(do_trick, "Maisey", trick="kangaroo")
    worker.work(burst=True)

    envelope = events[0]

    assert envelope["type"] == "transaction"
    assert envelope["contexts"]["trace"]["op"] == "rq.task"
    assert envelope["transaction"] == "tests.integrations.rq.test_rq.do_trick"
    assert envelope["extra"]["rq-job"] == DictionaryContaining(
        {
            "args": ["Maisey"],
            "kwargs": {"trick": "kangaroo"},
            "func": "tests.integrations.rq.test_rq.do_trick",
            "description": "tests.integrations.rq.test_rq.do_trick('Maisey', trick='kangaroo')",
        }
    )


def test_traces_sampler_gets_correct_values_in_sampling_context(
    sentry_init, DictionaryContaining, ObjectDescribedBy  # noqa:N803
):
    traces_sampler = mock.Mock(return_value=True)
    sentry_init(integrations=[RqIntegration()], traces_sampler=traces_sampler)

    queue = rq.Queue(connection=FakeStrictRedis())
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
    rq.__version__.split(".") < ["1", "5"], reason="At least rq-1.5 required"
)
def test_job_with_retries(sentry_init, capture_events):
    sentry_init(integrations=[RqIntegration()])
    events = capture_events()

    queue = rq.Queue(connection=FakeStrictRedis())
    worker = rq.SimpleWorker([queue], connection=queue.connection)

    queue.enqueue(crashing_job, foo=42, retry=rq.Retry(max=1))
    worker.work(burst=True)

    assert len(events) == 1


def test_version_parsing():
    integration = RqIntegration()
    # Testing version parser with various versions of Rq
    versions = [
        ("1.10.1", (1, 10, 1)),
        ("0.3.13", (0, 3, 13)),
    ]
    for _input, expected in versions:
        assert integration.parse_version(_input) == expected
