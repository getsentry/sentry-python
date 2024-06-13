import pytest
import sys
from sentry_sdk import Scope
from sentry_sdk.integrations.spark.spark_driver import (
    _set_app_properties,
    _start_sentry_listener,
    SentryListener,
    SparkIntegration,
)
from sentry_sdk.integrations.spark.spark_worker import SparkWorkerIntegration

from pyspark import SparkContext

from py4j.protocol import Py4JJavaError

################
# DRIVER TESTS #
################


def test_set_app_properties():
    spark_context = SparkContext(appName="Testing123")
    _set_app_properties()

    assert spark_context.getLocalProperty("sentry_app_name") == "Testing123"
    # applicationId generated by sparkContext init
    assert (
        spark_context.getLocalProperty("sentry_application_id")
        == spark_context.applicationId
    )


def test_start_sentry_listener():
    spark_context = SparkContext.getOrCreate()

    gateway = spark_context._gateway
    assert gateway._callback_server is None

    _start_sentry_listener(spark_context)

    assert gateway._callback_server is not None


def test_initialize_spark_integration(sentry_init):
    sentry_init(integrations=[SparkIntegration()])
    SparkContext.getOrCreate()


@pytest.fixture
def sentry_listener():

    listener = SentryListener()

    return listener


def test_sentry_listener_on_job_start(sentry_init, sentry_listener):
    sentry_init(integrations=[SparkIntegration()])
    listener = sentry_listener

    class MockJobStart:
        def jobId(self):  # noqa: N802
            return "sample-job-id-start"

    mock_job_start = MockJobStart()
    listener.onJobStart(mock_job_start)

    crumb = Scope.get_isolation_scope()._breadcrumbs.pop()
    assert crumb["level"] == "info"
    assert "sample-job-id-start" in crumb["message"]


@pytest.mark.parametrize(
    "job_result, level", [("JobSucceeded", "info"), ("JobFailed", "warning")]
)
def test_sentry_listener_on_job_end(sentry_init, sentry_listener, job_result, level):
    sentry_init(integrations=[SparkIntegration()])
    listener = sentry_listener

    class MockJobResult:
        def toString(self):  # noqa: N802
            return job_result

    class MockJobEnd:
        def jobId(self):  # noqa: N802
            return "sample-job-id-end"

        def jobResult(self):  # noqa: N802
            result = MockJobResult()
            return result

    mock_job_end = MockJobEnd()
    listener.onJobEnd(mock_job_end)

    crumb = Scope.get_isolation_scope()._breadcrumbs.pop()
    assert crumb["level"] == level
    assert crumb["data"]["result"] == job_result
    assert "sample-job-id-end" in crumb["message"]


def test_sentry_listener_on_stage_submitted(sentry_init, sentry_listener):
    sentry_init(integrations=[SparkIntegration()])
    listener = sentry_listener

    class StageInfo:
        def stageId(self):  # noqa: N802
            return "sample-stage-id-submit"

        def name(self):
            return "run-job"

        def attemptId(self):  # noqa: N802
            return 14

    class MockStageSubmitted:
        def stageInfo(self):  # noqa: N802
            stageinf = StageInfo()
            return stageinf

    mock_stage_submitted = MockStageSubmitted()
    listener.onStageSubmitted(mock_stage_submitted)

    crumb = Scope.get_isolation_scope()._breadcrumbs.pop()
    assert crumb["level"] == "info"
    assert "sample-stage-id-submit" in crumb["message"]
    assert crumb["data"]["attemptId"] == 14
    assert crumb["data"]["name"] == "run-job"


@pytest.fixture
def get_mock_stage_completed():
    def _inner(failure_reason):
        class JavaException:
            def __init__(self):
                self._target_id = "id"

        class FailureReason:
            def get(self):
                if failure_reason:
                    return "failure-reason"
                else:
                    raise Py4JJavaError("msg", JavaException())

        class StageInfo:
            def stageId(self):  # noqa: N802
                return "sample-stage-id-submit"

            def name(self):
                return "run-job"

            def attemptId(self):  # noqa: N802
                return 14

            def failureReason(self):  # noqa: N802
                return FailureReason()

        class MockStageCompleted:
            def stageInfo(self):  # noqa: N802
                return StageInfo()

        return MockStageCompleted()

    return _inner


def test_sentry_listener_on_stage_completed_success(
    sentry_init, sentry_listener, get_mock_stage_completed
):
    sentry_init(integrations=[SparkIntegration()])
    listener = sentry_listener

    mock_stage_completed = get_mock_stage_completed(failure_reason=False)
    listener.onStageCompleted(mock_stage_completed)

    crumb = Scope.get_isolation_scope()._breadcrumbs.pop()
    assert crumb["level"] == "info"
    assert "sample-stage-id-submit" in crumb["message"]
    assert crumb["data"]["attemptId"] == 14
    assert crumb["data"]["name"] == "run-job"
    assert "reason" not in crumb["data"]


def test_sentry_listener_on_stage_completed_failure(
    sentry_init, sentry_listener, get_mock_stage_completed
):
    sentry_init(integrations=[SparkIntegration()])
    listener = sentry_listener

    mock_stage_completed = get_mock_stage_completed(failure_reason=True)
    listener.onStageCompleted(mock_stage_completed)

    crumb = Scope.get_isolation_scope()._breadcrumbs.pop()
    assert crumb["level"] == "warning"
    assert "sample-stage-id-submit" in crumb["message"]
    assert crumb["data"]["attemptId"] == 14
    assert crumb["data"]["name"] == "run-job"
    assert crumb["data"]["reason"] == "failure-reason"


################
# WORKER TESTS #
################


def test_spark_worker(monkeypatch, sentry_init, capture_events, capture_exceptions):
    import pyspark.worker as original_worker
    import pyspark.daemon as original_daemon

    from pyspark.taskcontext import TaskContext

    task_context = TaskContext._getOrCreate()

    def mock_main():
        task_context._stageId = 0
        task_context._attemptNumber = 1
        task_context._partitionId = 2
        task_context._taskAttemptId = 3

        try:
            raise ZeroDivisionError
        except ZeroDivisionError:
            sys.exit(-1)

    monkeypatch.setattr(original_worker, "main", mock_main)

    sentry_init(integrations=[SparkWorkerIntegration()])

    events = capture_events()
    exceptions = capture_exceptions()

    original_daemon.worker_main()

    # SystemExit called, but not recorded as part of event
    assert type(exceptions.pop()) == SystemExit
    assert len(events[0]["exception"]["values"]) == 1
    assert events[0]["exception"]["values"][0]["type"] == "ZeroDivisionError"

    assert events[0]["tags"] == {
        "stageId": "0",
        "attemptNumber": "1",
        "partitionId": "2",
        "taskAttemptId": "3",
    }
