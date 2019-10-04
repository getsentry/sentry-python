from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.integrations import Integration


class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_spark_context_init()
        patch_spark_streaming_context_init()


def set_app_properties():
    from pyspark import SparkContext

    sparkContext = SparkContext._active_spark_context
    if sparkContext:
        sparkContext.setLocalProperty("app_name", sparkContext.appName)
        sparkContext.setLocalProperty("application_id", sparkContext.applicationId)


def patch_spark_streaming_context_init():
    from pyspark.streaming import StreamingContext, StreamingListener

    class SentryStreamingListener(StreamingListener):
        def onBatchSubmitted(self, batchSubmitted):
            # So workers in streaming batch have access to app_name and application_id
            set_app_properties()

    spark_streaming_context_init = StreamingContext.__init__

    def _sentry_patched_spark_streaming_context_init(self, *args, **kwargs):
        init = spark_streaming_context_init(self, *args, **kwargs)

        if Hub.current.get_integration(SparkIntegration) is None:
            return init

        # Set so initial workers have access to app_name and application_id
        set_app_properties()

        streamingListener = SentryStreamingListener()
        self.addStreamingListener(streamingListener)
        return init

    StreamingContext.__init__ = _sentry_patched_spark_streaming_context_init


def patch_spark_context_init():
    # type: () -> None
    from pyspark import SparkContext  # type: ignore
    from pyspark.java_gateway import ensure_callback_server_started  # type: ignore

    spark_context_init = SparkContext._do_init

    def _sentry_patched_spark_context_init(self, *args, **kwargs):
        init = spark_context_init(self, *args, **kwargs)

        if Hub.current.get_integration(SparkIntegration) is None:
            return init

        # So workers have access to app_name and id
        set_app_properties()

        gw = self._gateway
        ensure_callback_server_started(gw)
        listener = SentryListener()
        self._jsc.sc().addSparkListener(listener)

        with configure_scope() as scope:
            scope.user = {"id": self.sparkUser()}
            scope.set_tag("executor.id", self._conf.get("spark.executor.id"))
            scope.set_tag(
                "spark.submit.deployMode", self._conf.get("spark.submit.deployMode")
            )
            scope.set_tag("driver.port", self._conf.get("spark.driver.port"))
            scope.set_tag("driver.host", self._conf.get("spark.driver.host"))
            scope.set_tag("spark_version", self.version)
            scope.set_tag("app_name", self.appName)
            scope.set_tag("application_id", self.applicationId)

            scope.set_extra("start_time", self.startTime)
            scope.set_extra("web_url", self.uiWebUrl)

        return init

    SparkContext._do_init = _sentry_patched_spark_context_init


class SparkListener(object):
    def onApplicationEnd(self, applicationEnd):
        pass

    def onApplicationStart(self, applicationStart):
        pass

    def onBlockManagerAdded(self, blockManagerAdded):
        pass

    def onBlockManagerRemoved(self, blockManagerRemoved):
        pass

    def onBlockUpdated(self, blockUpdated):
        pass

    def onEnvironmentUpdate(self, environmentUpdate):
        pass

    def onExecutorAdded(self, executorAdded):
        pass

    def onExecutorBlacklisted(self, executorBlacklisted):
        pass

    def onExecutorBlacklistedForStage(self, executorBlacklistedForStage):
        pass

    def onExecutorMetricsUpdate(self, executorMetricsUpdate):
        pass

    def onExecutorRemoved(self, executorRemoved):
        pass

    def onJobEnd(self, jobEnd):
        pass

    def onJobStart(self, jobStart):
        pass

    def onNodeBlacklisted(self, nodeBlacklisted):
        pass

    def onNodeBlacklistedForStage(self, nodeBlacklistedForStage):
        pass

    def onNodeUnblacklisted(self, nodeUnblacklisted):
        pass

    def onOtherEvent(self, event):
        pass

    def onSpeculativeTaskSubmitted(self, speculativeTask):
        pass

    def onStageCompleted(self, stageCompleted):
        pass

    def onStageSubmitted(self, stageSubmitted):
        pass

    def onTaskEnd(self, taskEnd):
        pass

    def onTaskGettingResult(self, taskGettingResult):
        pass

    def onTaskStart(self, taskStart):
        pass

    def onUnpersistRDD(self, unpersistRDD):
        pass

    class Java:
        implements = ["org.apache.spark.scheduler.SparkListenerInterface"]


class SentryListener(SparkListener):
    def __init__(self):
        self.hub = Hub.current

    def onJobStart(self, jobStart):
        message = "Job {} Started".format(jobStart.jobId())
        self.hub.add_breadcrumb(level="info", message=message)

    def onJobEnd(self, jobEnd):
        message = "Job {} Ended".format(jobEnd.jobId())
        data = {"result": jobEnd.jobResult().toString()}
        level = "info" if jobEnd.jobResult().toString() == "JobSucceeded" else "warning"
        self.hub.add_breadcrumb(level=level, message=message, data=data)

    def onStageSubmitted(self, stageSubmitted):
        stageInfo = stageSubmitted.stageInfo()
        message = "Stage {} Submitted".format(stageInfo.stageId())
        data = {"attemptId": stageInfo.attemptId(), "name": stageInfo.name()}
        self.hub.add_breadcrumb(level="info", message=message, data=data)

    def onStageCompleted(self, stageCompleted):
        from py4j.protocol import Py4JJavaError

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
