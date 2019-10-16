from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import capture_internal_exceptions

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Any
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_spark_context_init()


def _set_app_properties():
    # type: () -> None
    """
    Set properties in driver that propagate to worker processes, allowing for workers to have access to those properties.
    This allows worker integration to have access to app_name and application_id.
    """
    from pyspark import SparkContext

    sparkContext = SparkContext._active_spark_context
    if sparkContext:
        sparkContext.setLocalProperty("sentry_app_name", sparkContext.appName)
        sparkContext.setLocalProperty(
            "sentry_application_id", sparkContext.applicationId
        )


def _start_sentry_listener(sc):
    # type: (Any) -> None
    """
    Start java gateway server to add custom `SparkListener`
    """
    from pyspark.java_gateway import ensure_callback_server_started

    gw = sc._gateway
    ensure_callback_server_started(gw)
    listener = SentryListener()
    sc._jsc.sc().addSparkListener(listener)


def patch_spark_context_init():
    # type: () -> None
    from pyspark import SparkContext

    spark_context_init = SparkContext._do_init

    def _sentry_patched_spark_context_init(self, *args, **kwargs):
        # type: (SparkContext, *Any, **Any) -> Optional[Any]
        init = spark_context_init(self, *args, **kwargs)

        if Hub.current.get_integration(SparkIntegration) is None:
            return init

        _start_sentry_listener(self)
        _set_app_properties()

        with configure_scope() as scope:

            @scope.add_event_processor
            def process_event(event, hint):
                # type: (Event, Hint) -> Optional[Event]
                with capture_internal_exceptions():
                    if Hub.current.get_integration(SparkIntegration) is None:
                        return event

                    event.setdefault("user", {}).setdefault("id", self.sparkUser())

                    event.setdefault("tags", {}).setdefault(
                        "executor.id", self._conf.get("spark.executor.id")
                    )
                    event["tags"].setdefault(
                        "spark-submit.deployMode",
                        self._conf.get("spark.submit.deployMode"),
                    )
                    event["tags"].setdefault(
                        "driver.host", self._conf.get("spark.driver.host")
                    )
                    event["tags"].setdefault(
                        "driver.port", self._conf.get("spark.driver.port")
                    )
                    event["tags"].setdefault("spark_version", self.version)
                    event["tags"].setdefault("app_name", self.appName)
                    event["tags"].setdefault("application_id", self.applicationId)
                    event["tags"].setdefault("master", self.master)
                    event["tags"].setdefault("spark_home", self.sparkHome)

                    event.setdefault("extra", {}).setdefault("web_url", self.uiWebUrl)

                return event

        return init

    SparkContext._do_init = _sentry_patched_spark_context_init


class SparkListener(object):
    def onApplicationEnd(self, applicationEnd):
        # type: (Any) -> None
        pass

    def onApplicationStart(self, applicationStart):
        # type: (Any) -> None
        pass

    def onBlockManagerAdded(self, blockManagerAdded):
        # type: (Any) -> None
        pass

    def onBlockManagerRemoved(self, blockManagerRemoved):
        # type: (Any) -> None
        pass

    def onBlockUpdated(self, blockUpdated):
        # type: (Any) -> None
        pass

    def onEnvironmentUpdate(self, environmentUpdate):
        # type: (Any) -> None
        pass

    def onExecutorAdded(self, executorAdded):
        # type: (Any) -> None
        pass

    def onExecutorBlacklisted(self, executorBlacklisted):
        # type: (Any) -> None
        pass

    def onExecutorBlacklistedForStage(self, executorBlacklistedForStage):
        # type: (Any) -> None
        pass

    def onExecutorMetricsUpdate(self, executorMetricsUpdate):
        # type: (Any) -> None
        pass

    def onExecutorRemoved(self, executorRemoved):
        # type: (Any) -> None
        pass

    def onJobEnd(self, jobEnd):
        # type: (Any) -> None
        pass

    def onJobStart(self, jobStart):
        # type: (Any) -> None
        pass

    def onNodeBlacklisted(self, nodeBlacklisted):
        # type: (Any) -> None
        pass

    def onNodeBlacklistedForStage(self, nodeBlacklistedForStage):
        # type: (Any) -> None
        pass

    def onNodeUnblacklisted(self, nodeUnblacklisted):
        # type: (Any) -> None
        pass

    def onOtherEvent(self, event):
        # type: (Any) -> None
        pass

    def onSpeculativeTaskSubmitted(self, speculativeTask):
        # type: (Any) -> None
        pass

    def onStageCompleted(self, stageCompleted):
        # type: (Any) -> None
        pass

    def onStageSubmitted(self, stageSubmitted):
        # type: (Any) -> None
        pass

    def onTaskEnd(self, taskEnd):
        # type: (Any) -> None
        pass

    def onTaskGettingResult(self, taskGettingResult):
        # type: (Any) -> None
        pass

    def onTaskStart(self, taskStart):
        # type: (Any) -> None
        pass

    def onUnpersistRDD(self, unpersistRDD):
        # type: (Any) -> None
        pass

    class Java:
        implements = ["org.apache.spark.scheduler.SparkListenerInterface"]


class SentryListener(SparkListener):
    def __init__(self):
        # type: () -> None
        self.hub = Hub.current

    def onJobStart(self, jobStart):
        # type: (Any) -> None
        message = "Job {} Started".format(jobStart.jobId())
        self.hub.add_breadcrumb(level="info", message=message)
        _set_app_properties()

    def onJobEnd(self, jobEnd):
        # type: (Any) -> None
        level = ""
        message = ""
        data = {"result": jobEnd.jobResult().toString()}

        if jobEnd.jobResult().toString() == "JobSucceeded":
            level = "info"
            message = "Job {} Ended".format(jobEnd.jobId())
        else:
            level = "warning"
            message = "Job {} Failed".format(jobEnd.jobId())

        self.hub.add_breadcrumb(level=level, message=message, data=data)

    def onStageSubmitted(self, stageSubmitted):
        # type: (Any) -> None
        stageInfo = stageSubmitted.stageInfo()
        message = "Stage {} Submitted".format(stageInfo.stageId())
        data = {"attemptId": stageInfo.attemptId(), "name": stageInfo.name()}
        self.hub.add_breadcrumb(level="info", message=message, data=data)
        _set_app_properties()

    def onStageCompleted(self, stageCompleted):
        # type: (Any) -> None
        from py4j.protocol import Py4JJavaError  # type: ignore

        stageInfo = stageCompleted.stageInfo()
        message = ""
        level = ""
        data = {"attemptId": stageInfo.attemptId(), "name": stageInfo.name()}

        # Have to Try Except because stageInfo.failureReason() is typed with Scala Option
        try:
            data["reason"] = stageInfo.failureReason().get()
            message = "Stage {} Failed".format(stageInfo.stageId())
            level = "warning"
        except Py4JJavaError:
            message = "Stage {} Completed".format(stageInfo.stageId())
            level = "info"

        self.hub.add_breadcrumb(level=level, message=message, data=data)
