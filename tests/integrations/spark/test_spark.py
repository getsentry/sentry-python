import pytest

import pdb

from sentry_sdk.integrations.spark.spark_driver import (
    _set_app_properties,
    _start_sentry_listener,
    SentryListener,
)

pytest.importorskip("pyspark")

from pyspark import SparkContext


def test_set_app_properties():
    sparkContext = SparkContext(appName="Testing123")
    _set_app_properties()

    assert sparkContext.getLocalProperty("app_name") == "Testing123"
    # applicationId generated by sparkContext init
    assert sparkContext.getLocalProperty("application_id") == sparkContext.applicationId


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
    from py4j.protocol import Py4JJavaError

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
