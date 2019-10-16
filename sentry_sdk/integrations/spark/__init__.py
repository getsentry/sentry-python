from sentry_sdk import configure_scope
from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
from sentry_sdk.integrations import Integration

SCOPE_TAGS = frozenset(("startTime"))

class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        # type: () -> None
        patch_spark_context()

def patch_spark_context():
    from pyspark import SparkContext # type: ignore
    from pyspark.java_gateway import ensure_callback_server_started # type: ignore
    from py4j.java_gateway import java_import # type: ignore

    spark_context_init = SparkContext._do_init
    
    hub = Hub.current

    def _sentry_patched_spark_context_init(self, *args, **kwargs):
        init = spark_context_init(self, *args, **kwargs)
        sc = SparkContext.getOrCreate()

        gw = sc._gateway
        ensure_callback_server_started(gw)
        listener = SentryListener(hub)
        sc._jsc.sc().addSparkListener(listener)

        with configure_scope() as scope:
            scope.user = { "id": self.sparkUser() }
            scope.set_tag("executor.id", self._conf.get("spark.executor.id"))
            scope.set_tag("spark.submit.deployMode", self._conf.get("spark.submit.deployMode"))
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
    def onBlockManagerRemoved(self, blockManagerRemoved):
        pass
    def onBlockUpdated(self, blockUpdated):
        pass
    def onEnvironmentUpdate(self, environmentUpdate):
        pass
    def onExecutorAdded(self, executorAdded):
        pass
    def onExecutorMetricsUpdate(self, executorMetricsUpdate):
        pass
    def onExecutorRemoved(self, executorRemoved):
        pass
    def onJobEnd(self, jobEnd):
        pass
    def onJobStart(self, jobStart):
        pass
    def onOtherEvent(self, event):
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
    current_hub = None

    def __init__(self, hub):
        self.current_hub = hub

    def onJobStart(self, jobStart):
        message  = "Job {} Started".format(jobStart.jobId())
        self.current_hub.add_breadcrumb(level="info", message=message)

    def onJobEnd(self, jobEnd):
        message = "Job {} Ended".format(jobEnd.jobId())
        data = { "result": jobEnd.jobResult().toString() }
        level = "info" if jobEnd.jobResult().toString() == "JobSucceeded" else "warning"
        self.current_hub.add_breadcrumb(level=level, message=message, data=data)
    
    def onStageSubmitted(self, stageSubmitted):
        stageInfo = stageSubmitted.stageInfo()
        message = "Stage {} Submitted".format(stageInfo.stageId())
        data = { "attemptId": stageInfo.attemptId(), "name": stageInfo.name() }
        self.current_hub.add_breadcrumb(level="info", message=message, data=data)
    
    def onStageCompleted(self, stageCompleted):
        from py4j.protocol import Py4JJavaError

        stageInfo = stageCompleted.stageInfo()
        message = ""
        failureReason = ""
        level = ""
        data = { "attemptId": stageInfo.attemptId(), "name": stageInfo.name() }
        
        # Have to Try Except because stageInfo.failureReason() is typed with Scala Option
        try:
            message = "Stage {} Failed".format(stageInfo.stageId())
            data["reason"] = stageInfo.failureReason().get()
            level = "warning"
        except Py4JJavaError:
            message = "Stage {} Completed".format(stageInfo.stageId())
            level = "info"
    
        self.current_hub.add_breadcrumb(level=level, message=message, data=data)
