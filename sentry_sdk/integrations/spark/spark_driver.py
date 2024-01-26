from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import capture_internal_exceptions

from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Optional

    from sentry_sdk._types import Event, Hint


class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once() -> None:
        patch_spark_context_init()


def _set_app_properties() -> None:
    """
    Set properties in driver that propagate to worker processes, allowing for workers to have access to those properties.
    This allows worker integration to have access to app_name and application_id.
    """
    from pyspark import SparkContext

    spark_context = SparkContext._active_spark_context
    if spark_context:
        spark_context.setLocalProperty("sentry_app_name", spark_context.appName)
        spark_context.setLocalProperty(
            "sentry_application_id", spark_context.applicationId
        )


def _start_sentry_listener(sc: Any) -> None:
    """
    Start java gateway server to add custom `SparkListener`
    """
    from pyspark.java_gateway import ensure_callback_server_started

    gw = sc._gateway
    ensure_callback_server_started(gw)
    listener = SentryListener()
    sc._jsc.sc().addSparkListener(listener)


def patch_spark_context_init() -> None:
    from pyspark import SparkContext

    spark_context_init = SparkContext._do_init

    def _sentry_patched_spark_context_init(
        self: SparkContext, *args: Any, **kwargs: Any
    ) -> Optional[Any]:
        init = spark_context_init(self, *args, **kwargs)

        if Hub.current.get_integration(SparkIntegration) is None:
            return init

        _start_sentry_listener(self)
        _set_app_properties()

        with configure_scope() as scope:

            @scope.add_event_processor
            def process_event(event: Event, hint: Hint) -> Optional[Event]:
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


class SparkListener:
    def onApplicationEnd(self, applicationEnd: Any) -> None:  # noqa: N802,N803
        pass

    def onApplicationStart(self, applicationStart: Any) -> None:  # noqa: N802,N803
        pass

    def onBlockManagerAdded(self, blockManagerAdded: Any) -> None:  # noqa: N802,N803
        pass

    def onBlockManagerRemoved(
        self, blockManagerRemoved: Any
    ) -> None:  # noqa: N802,N803
        pass

    def onBlockUpdated(self, blockUpdated: Any) -> None:  # noqa: N802,N803
        pass

    def onEnvironmentUpdate(self, environmentUpdate: Any) -> None:  # noqa: N802,N803
        pass

    def onExecutorAdded(self, executorAdded: Any) -> None:  # noqa: N802,N803
        pass

    def onExecutorBlacklisted(
        self, executorBlacklisted: Any
    ) -> None:  # noqa: N802,N803
        pass

    def onExecutorBlacklistedForStage(  # noqa: N802
        self, executorBlacklistedForStage: Any  # noqa: N803
    ) -> None:
        pass

    def onExecutorMetricsUpdate(
        self, executorMetricsUpdate: Any
    ) -> None:  # noqa: N802,N803
        pass

    def onExecutorRemoved(self, executorRemoved: Any) -> None:  # noqa: N802,N803
        pass

    def onJobEnd(self, jobEnd: Any) -> None:  # noqa: N802,N803
        pass

    def onJobStart(self, jobStart: Any) -> None:  # noqa: N802,N803
        pass

    def onNodeBlacklisted(self, nodeBlacklisted: Any) -> None:  # noqa: N802,N803
        pass

    def onNodeBlacklistedForStage(
        self, nodeBlacklistedForStage: Any
    ) -> None:  # noqa: N802,N803
        pass

    def onNodeUnblacklisted(self, nodeUnblacklisted: Any) -> None:  # noqa: N802,N803
        pass

    def onOtherEvent(self, event: Any) -> None:  # noqa: N802,N803
        pass

    def onSpeculativeTaskSubmitted(
        self, speculativeTask: Any
    ) -> None:  # noqa: N802,N803
        pass

    def onStageCompleted(self, stageCompleted: Any) -> None:  # noqa: N802,N803
        pass

    def onStageSubmitted(self, stageSubmitted: Any) -> None:  # noqa: N802,N803
        pass

    def onTaskEnd(self, taskEnd: Any) -> None:  # noqa: N802,N803
        pass

    def onTaskGettingResult(self, taskGettingResult: Any) -> None:  # noqa: N802,N803
        pass

    def onTaskStart(self, taskStart: Any) -> None:  # noqa: N802,N803
        pass

    def onUnpersistRDD(self, unpersistRDD: Any) -> None:  # noqa: N802,N803
        pass

    class Java:
        implements = ["org.apache.spark.scheduler.SparkListenerInterface"]


class SentryListener(SparkListener):
    def __init__(self) -> None:
        self.hub = Hub.current

    def onJobStart(self, jobStart: Any) -> None:  # noqa: N802,N803
        message = "Job {} Started".format(jobStart.jobId())
        self.hub.add_breadcrumb(level="info", message=message)
        _set_app_properties()

    def onJobEnd(self, jobEnd: Any) -> None:  # noqa: N802,N803
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

    def onStageSubmitted(self, stageSubmitted: Any) -> None:  # noqa: N802,N803
        stage_info = stageSubmitted.stageInfo()
        message = "Stage {} Submitted".format(stage_info.stageId())
        data = {"attemptId": stage_info.attemptId(), "name": stage_info.name()}
        self.hub.add_breadcrumb(level="info", message=message, data=data)
        _set_app_properties()

    def onStageCompleted(self, stageCompleted: Any) -> None:  # noqa: N802,N803
        from py4j.protocol import Py4JJavaError  # type: ignore

        stage_info = stageCompleted.stageInfo()
        message = ""
        level = ""
        data = {"attemptId": stage_info.attemptId(), "name": stage_info.name()}

        # Have to Try Except because stageInfo.failureReason() is typed with Scala Option
        try:
            data["reason"] = stage_info.failureReason().get()
            message = "Stage {} Failed".format(stage_info.stageId())
            level = "warning"
        except Py4JJavaError:
            message = "Stage {} Completed".format(stage_info.stageId())
            level = "info"

        self.hub.add_breadcrumb(level=level, message=message, data=data)
