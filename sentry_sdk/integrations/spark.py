from __future__ import absolute_import

from functools import wraps
import pdb

from sentry_sdk.hub import Hub
from sentry_sdk.utils import event_from_exception
from sentry_sdk._compat import reraise

from sentry_sdk import configure_scope, push_scope, add_breadcrumb, capture_exception
from sentry_sdk.integrations import Integration
from sentry_sdk.utils import logger

SCOPE_TAGS = frozenset(("startTime"))

class SparkIntegration(Integration):
    identifier = "spark"

    @staticmethod
    def setup_once():
        # type: () -> None
        # patch_spark_context_getOrCreate()
        patch_spark_utils()
        patch_spark_worker()
        patch_spark_context()


def raise_exception(client):
    """
    Raise an exception. If the client is not in the hub, rebind it.
    """
    hub = Hub.current
    if hub.client is None:
        hub.bind_client(client)
    exc_info = sys.exc_info()
    with capture_internal_exceptions():
        _capture_exception(exc_info, hub)
    reraise(*exc_info)


def patch_spark_utils():
    from pyspark.util import fail_on_stopiteration
    client = Hub.current.client

    old_fail_on_stopiteration = fail_on_stopiteration

    def _sentry_patched_fail_on_stopiteration(*args, **kwargs):
        try:
            return fail_on_stopiteration(*args, **kwargs)
        except Exception:
            raise_exception(client)
    fail_on_stopiteration = _sentry_patched_fail_on_stopiteration


def patch_spark_worker():
    from pyspark.worker import main

    old_main = main

    def _sentry_patched_worker_main(*args, **kwargs):
        try:
            old_main(*args, **kwargs)
        except Exception as e:
            capture_exception(e)
    main = _sentry_patched_worker_main


# def patch_spark_context_getOrCreate():
#     from pyspark import SparkContext
#     from pyspark.java_gateway import ensure_callback_server_started
#     from py4j.java_gateway import java_import, CallbackServerParameters

#     spark_context_getOrCreate = SparkContext.getOrCreate

#     def _sentry_patched_spark_context_getOrCreate(self, *args, **kwargs):
#         sc = spark_context_getOrCreate(self, *args, **kwargs)
        
#         java_import(sc._gateway.jvm, "org.apache.spark.scheduler.*")
#         # ensure_callback_server_started(sc._gateway)
#         # gw = sc._gateway
#         # ensure_callback_server_started(gw)
#         listener = SentryListener()
#         sc._jsc.sc().addSparkListener(listener)
#         sc.parallelize(range(100), 3).saveAsTextFile("/tmp/listener_test_simple")
#         # log4jLogger = sc._jvm.org.apache.log4j
#         # LOGGER = log4jLogger.LogManager.getLogger(__name__)
#         # LOGGER.info("hello from py world")
#         sc._gateway.shutdown_callback_server()
#         return sc

#     SparkContext.getOrCreate = _sentry_patched_spark_context_getOrCreate


def patch_spark_context():
    from pyspark import SparkContext # type: ignore

    spark_context_init = SparkContext._do_init

    def _sentry_patched_spark_context_init(self, *args, **kwargs):
        spark_context_init(self, *args, **kwargs)

        # gw = SparkContext._gateway

        # gw.start_callback_server()
        # self.parallelize(range(100), 3).saveAsTextFile("/tmp/listener_test_simple")

        with configure_scope() as scope:
            scope.set_tag("user", self.sparkUser())
            scope.set_tag("spark_version", self.version)
            scope.set_tag("app_name", self.appName)

            scope.set_extra("start_time", self.startTime)
            scope.set_extra("web_url", self.uiWebUrl)

    
    #def _sentry_patched_spark_context_

    # try:
    SparkContext._do_init = _sentry_patched_spark_context_init
    # except Exception:
    #     _capture_and_reraise()
        # SparkContext._gateway.shutdown_callback_server()

#        listener = SentryListener()
        #self._jsc.sc().addSparkListener(listener)
# def _capture_and_reraise():
#     exc_info = sys.exc_info()
#     hub = Hub.current
#     if hub is not None and hub.client is not None:
#         event, hint = event_from_exception(
#             exc_info,
#             client_options=hub.client.options,
#             mechanism={"type": "spark", "handled": False},
#         )
#         hub.capture_event(event, hint=hint)

#     reraise(*exc_info)

# def patch_pyspark_java_gateway():
#     from py4j.java_gateway import java_import
#     from pyspark.java_gateway import launch_gateway

#     old_launch_gateway = launch_gateway

#     def _sentry_patched_launch_gateway(self, *args, **kwargs):
#         gateway = old_launch_gateway(self, *args, **kwargs)
#         java_import(gateway.jvm, "org.apache.spark.scheduler")
#         return gateway

#     launch_gateway = _sentry_patched_launch_gateway

class PythonListener(object):
    package = "net.zero323.spark.examples.listener"

    @staticmethod
    def get_manager():
        jvm = SparkContext.getOrCreate()._jvm
        manager = getattr(jvm, "{}.{}".format(PythonListener.package, "Manager"))
        return manager

    def __init__(self):
        self.uuid = None

    def notify(self, obj):
        """This method is required by Scala Listener interface
        we defined above.
        """
        print(obj)

    def register(self):
        manager = PythonListener.get_manager()
        self.uuid = manager.register(self)
        return self.uuid

    def unregister(self):
        manager =  PythonListener.get_manager()
        manager.unregister(self.uuid)
        self.uuid = None

    class Java:
        implements = ["net.zero323.spark.examples.listener.Listener"]

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
    def onApplicationStart(self, applicationStart):
        with push_scope():
            add_breadcrumb({"appName": applicationStart.appName})