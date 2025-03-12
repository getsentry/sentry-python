import pytest
import sys
from unittest.mock import patch

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


@pytest.fixture(scope="function")
def sentry_init_with_reset(sentry_init):
    from sentry_sdk.integrations import _processed_integrations

    yield lambda: sentry_init(integrations=[SparkIntegration()])
    _processed_integrations.remove("spark")


@pytest.fixture(scope="function")
def create_spark_context():
    yield lambda: SparkContext(appName="Testing123")
    SparkContext._active_spark_context.stop()


def test_set_app_properties(create_spark_context):
    spark_context = create_spark_context()
    _set_app_properties()

    assert spark_context.getLocalProperty("sentry_app_name") == "Testing123"
    # applicationId generated by sparkContext init
    assert (
        spark_context.getLocalProperty("sentry_application_id")
        == spark_context.applicationId
    )


def test_start_sentry_listener(create_spark_context):
    spark_context = create_spark_context()
    gateway = spark_context._gateway
    assert gateway._callback_server is None

    _start_sentry_listener(spark_context)

    assert gateway._callback_server is not None


@patch("sentry_sdk.integrations.spark.spark_driver._patch_spark_context_init")
def test_initialize_spark_integration_before_spark_context_init(
    mock_patch_spark_context_init,
    sentry_init_with_reset,
    create_spark_context,
):
    sentry_init_with_reset()
    create_spark_context()

    mock_patch_spark_context_init.assert_called_once()


@patch("sentry_sdk.integrations.spark.spark_driver._activate_integration")
def test_initialize_spark_integration_after_spark_context_init(
    mock_activate_integration,
    create_spark_context,
    sentry_init_with_reset,
):
    create_spark_context()
    sentry_init_with_reset()

    mock_activate_integration.assert_called_once()


@pytest.fixture
def sentry_listener():

    listener = SentryListener()

    return listener


def test_sentry_listener_on_job_start(sentry_listener):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:

        class MockJobStart:
            def jobId(self):  # noqa: N802
                return "sample-job-id-start"

        mock_job_start = MockJobStart()
        listener.onJobStart(mock_job_start)

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "info"
        assert "sample-job-id-start" in mock_hub.kwargs["message"]


@pytest.mark.parametrize(
    "job_result, level", [("JobSucceeded", "info"), ("JobFailed", "warning")]
)
def test_sentry_listener_on_job_end(sentry_listener, job_result, level):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:

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

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == level
        assert mock_hub.kwargs["data"]["result"] == job_result
        assert "sample-job-id-end" in mock_hub.kwargs["message"]


def test_sentry_listener_on_stage_submitted(sentry_listener):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:

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

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "info"
        assert "sample-stage-id-submit" in mock_hub.kwargs["message"]
        assert mock_hub.kwargs["data"]["attemptId"] == 14
        assert mock_hub.kwargs["data"]["name"] == "run-job"


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
    sentry_listener, get_mock_stage_completed
):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:
        mock_stage_completed = get_mock_stage_completed(failure_reason=False)
        listener.onStageCompleted(mock_stage_completed)

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "info"
        assert "sample-stage-id-submit" in mock_hub.kwargs["message"]
        assert mock_hub.kwargs["data"]["attemptId"] == 14
        assert mock_hub.kwargs["data"]["name"] == "run-job"
        assert "reason" not in mock_hub.kwargs["data"]


def test_sentry_listener_on_stage_completed_failure(
    sentry_listener, get_mock_stage_completed
):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:
        mock_stage_completed = get_mock_stage_completed(failure_reason=True)
        listener.onStageCompleted(mock_stage_completed)

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "warning"
        assert "sample-stage-id-submit" in mock_hub.kwargs["message"]
        assert mock_hub.kwargs["data"]["attemptId"] == 14
        assert mock_hub.kwargs["data"]["name"] == "run-job"
        assert mock_hub.kwargs["data"]["reason"] == "failure-reason"


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


def test_sentry_listener_on_stage_submitted_no_attempt_id(sentry_listener):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:

        class StageInfo:
            def stageId(self):  # noqa: N802
                return "sample-stage-id-submit"

            def name(self):
                return "run-job"

            def attemptName(self):  # noqa: N802
                return 14

        class MockStageSubmitted:
            def stageInfo(self):  # noqa: N802
                stageinf = StageInfo()
                return stageinf

        mock_stage_submitted = MockStageSubmitted()
        listener.onStageSubmitted(mock_stage_submitted)

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "info"
        assert "sample-stage-id-submit" in mock_hub.kwargs["message"]
        assert mock_hub.kwargs["data"]["attemptId"] == 14
        assert mock_hub.kwargs["data"]["name"] == "run-job"


def test_sentry_listener_on_stage_submitted_no_attempt_id_or_number(sentry_listener):
    listener = sentry_listener
    with patch.object(listener, "_add_breadcrumb") as mock_add_breadcrumb:

        class StageInfo:
            def stageId(self):  # noqa: N802
                return "sample-stage-id-submit"

            def name(self):
                return "run-job"

        class MockStageSubmitted:
            def stageInfo(self):  # noqa: N802
                stageinf = StageInfo()
                return stageinf

        mock_stage_submitted = MockStageSubmitted()
        listener.onStageSubmitted(mock_stage_submitted)

        mock_add_breadcrumb.assert_called_once()
        mock_hub = mock_add_breadcrumb.call_args

        assert mock_hub.kwargs["level"] == "info"
        assert "sample-stage-id-submit" in mock_hub.kwargs["message"]
        assert "attemptId" not in mock_hub.kwargs["data"]
        assert mock_hub.kwargs["data"]["name"] == "run-job"
