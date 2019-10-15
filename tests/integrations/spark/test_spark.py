import pytest
import sys

from sentry_sdk import configure_scope
from sentry_sdk.integrations.spark.spark_driver import (
    _set_app_properties,
    _start_sentry_listener,
    SentryListener,
    SparkIntegration,
)

from sentry_sdk.integrations.spark.spark_worker import SparkWorkerIntegration


pytest.importorskip("pyspark")
pytest.importorskip("py4j")

from pyspark import SparkContext
from pyspark.sql import SparkSession

from py4j.protocol import Py4JJavaError

################
# DRIVER TESTS #
################


def test_set_app_properties():
    sparkContext = SparkContext(appName="Testing123")
    _set_app_properties()

    assert sparkContext.getLocalProperty("sentry_app_name") == "Testing123"
    # applicationId generated by sparkContext init
    assert (
        sparkContext.getLocalProperty("sentry_application_id")
        == sparkContext.applicationId
    )


def test_start_sentry_listener():
    sparkContext = SparkContext.getOrCreate()

    gateway = sparkContext._gateway
    assert gateway._callback_server is None

    _start_sentry_listener(sparkContext)

    assert gateway._callback_server is not None


@pytest.fixture
def sentry_listener(monkeypatch):
    class MockHub:
        def __init__(self):
            self.args = []
            self.kwargs = {}

        def add_breadcrumb(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    listener = SentryListener()
    mockHub = MockHub()

    monkeypatch.setattr(listener, "hub", mockHub)

    return listener, mockHub


def test_sentry_listener_on_job_start(sentry_listener):
    listener, mockHub = sentry_listener

    class MockJobStart:
        def jobId(self):
            return "sample-job-id-start"

    mockJobStart = MockJobStart()
    listener.onJobStart(mockJobStart)

    assert mockHub.kwargs["level"] == "info"
    assert "sample-job-id-start" in mockHub.kwargs["message"]


@pytest.mark.parametrize(
    "job_result, level", [("JobSucceeded", "info"), ("JobFailed", "warning")]
)
def test_sentry_listener_on_job_end(sentry_listener, job_result, level):
    listener, mockHub = sentry_listener

    class MockJobResult:
        def toString(self):
            return job_result

    class MockJobEnd:
        def jobId(self):
            return "sample-job-id-end"

        def jobResult(self):
            result = MockJobResult()
            return result

    mockJobEnd = MockJobEnd()
    listener.onJobEnd(mockJobEnd)

    assert mockHub.kwargs["level"] == level
    assert mockHub.kwargs["data"]["result"] == job_result
    assert "sample-job-id-end" in mockHub.kwargs["message"]


def test_sentry_listener_on_stage_submitted(sentry_listener):
    listener, mockHub = sentry_listener

    class StageInfo:
        def stageId(self):
            return "sample-stage-id-submit"

        def name(self):
            return "run-job"

        def attemptId(self):
            return 14

    class MockStageSubmitted:
        def stageInfo(self):
            stageinf = StageInfo()
            return stageinf

    mockStageSubmitted = MockStageSubmitted()
    listener.onStageSubmitted(mockStageSubmitted)

    assert mockHub.kwargs["level"] == "info"
    assert "sample-stage-id-submit" in mockHub.kwargs["message"]
    assert mockHub.kwargs["data"]["attemptId"] == 14
    assert mockHub.kwargs["data"]["name"] == "run-job"


@pytest.fixture
def get_mock_stage_completed():
    def _inner(failureReason):
        class JavaException:
            def __init__(self):
                self._target_id = "id"

        class FailureReason:
            def get(self):
                if failureReason:
                    return "failure-reason"
                else:
                    raise Py4JJavaError("msg", JavaException())

        class StageInfo:
            def stageId(self):
                return "sample-stage-id-submit"

            def name(self):
                return "run-job"

            def attemptId(self):
                return 14

            def failureReason(self):
                return FailureReason()

        class MockStageCompleted:
            def stageInfo(self):
                return StageInfo()

        return MockStageCompleted()

    return _inner


def test_sentry_listener_on_stage_completed_success(
    sentry_listener, get_mock_stage_completed
):
    listener, mockHub = sentry_listener

    mockStageCompleted = get_mock_stage_completed(failureReason=False)
    listener.onStageCompleted(mockStageCompleted)

    assert mockHub.kwargs["level"] == "info"
    assert "sample-stage-id-submit" in mockHub.kwargs["message"]
    assert mockHub.kwargs["data"]["attemptId"] == 14
    assert mockHub.kwargs["data"]["name"] == "run-job"
    assert "reason" not in mockHub.kwargs["data"]


def test_sentry_listener_on_stage_completed_failure(
    sentry_listener, get_mock_stage_completed
):
    listener, mockHub = sentry_listener

    mockStageCompleted = get_mock_stage_completed(failureReason=True)
    listener.onStageCompleted(mockStageCompleted)

    assert mockHub.kwargs["level"] == "warning"
    assert "sample-stage-id-submit" in mockHub.kwargs["message"]
    assert mockHub.kwargs["data"]["attemptId"] == 14
    assert mockHub.kwargs["data"]["name"] == "run-job"
    assert mockHub.kwargs["data"]["reason"] == "failure-reason"


# Based on https://github.com/apache/spark/blob/master/examples/src/main/python/pi.py
@pytest.fixture
def pi_job():
    def inner():
        from random import random

        partitions = 2
        n = 100000 * partitions

        spark = SparkSession.builder.appName("PythonPi").getOrCreate()

        def f(_):
            x = random() * 2 - 1
            y = random() * 2 - 1
            return 1 if x ** 2 + y ** 2 <= 1 else 0

        def add(a, b):
            n = 5 / 0
            return a + b + n

        count = (
            spark.sparkContext.parallelize(range(1, n + 1), partitions)
            .map(f)
            .reduce(add)
        )
        print("Pi is roughly %f" % (4.0 * count / n))

    return inner


def test_spark_context_breadcrumbs(sentry_init, pi_job):
    sentry_init(integrations=[SparkIntegration()])

    with pytest.raises(Py4JJavaError):
        pi_job()

    with configure_scope() as scope:
        assert scope._breadcrumbs[1]["level"] == "info"
        assert scope._breadcrumbs[1]["message"] == "Job 0 Started"

        assert scope._breadcrumbs[2]["level"] == "info"
        assert scope._breadcrumbs[2]["message"] == "Stage 0 Submitted"
        assert "reduce" in scope._breadcrumbs[2]["data"]["name"]

        assert scope._breadcrumbs[3]["level"] == "warning"
        assert scope._breadcrumbs[3]["message"] == "Stage 0 Failed"
        assert "reduce" in scope._breadcrumbs[3]["data"]["name"]
        assert "ZeroDivisionError" in scope._breadcrumbs[3]["data"]["reason"]

        assert scope._breadcrumbs[4]["level"] == "warning"
        assert scope._breadcrumbs[4]["message"] == "Job 0 Failed"
        assert "ZeroDivisionError" in scope._breadcrumbs[4]["data"]["result"]


################
# WORKER TESTS #
################


def test_spark_worker(monkeypatch, sentry_init, capture_events, capture_exceptions):
    import pyspark.worker as original_worker
    import pyspark.daemon as original_daemon

    from pyspark.taskcontext import TaskContext

    taskContext = TaskContext._getOrCreate()

    def mockMain():
        taskContext._stageId = 0
        taskContext._attemptNumber = 1
        taskContext._partitionId = 2
        taskContext._taskAttemptId = 3

        try:
            raise ZeroDivisionError
        except ZeroDivisionError:
            sys.exit(-1)

    monkeypatch.setattr(original_worker, "main", mockMain)

    sentry_init(integrations=[SparkWorkerIntegration()])

    events = capture_events()
    exceptions = capture_exceptions()

    original_daemon.worker_main()

    # SystemExit called, but not recorded as part of event
    assert type(exceptions.pop()) == SystemExit
    assert len(events[0]["exception"]["values"]) == 1
    assert events[0]["exception"]["values"][0]["type"] == "ZeroDivisionError"

    assert events[0]["tags"] == {
        "stageId": 0,
        "attemptNumber": 1,
        "partitionId": 2,
        "taskAttemptId": 3,
    }
