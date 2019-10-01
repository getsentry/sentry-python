# import os
# import imp

import sentry_sdk
from sentry_sdk.hub import Hub
from sentry_sdk.utils import capture_internal_exceptions, event_from_exception
# from sentry_sdk.integrations import SparkIntegration

# # This is a hack to always refer the main code rather than built zip.
# # main_code_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# # daemon = imp.load_source("daemon", "%s/pyspark/daemon.py" % main_code_dir)
# # worker = imp.load_source("worker", "%s/pyspark/worker.py" % main_code_dir)

def _capture_exception(exc_info, hub):
    """
    Send Beam exception to Sentry.
    """
    integration = hub.get_integration(SparkIntegration)
    if integration:
        client = hub.client
        event, hint = event_from_exception(
            exc_info,
            client_options=client.options,
            mechanism={"type": "spark", "handled": False},
        )
        hub.capture_event(event, hint=hint)


def raise_exception(client):
    """
    Raise an exception. If the client is not in the hub, rebind it.
    """
    hub = Hub.current
    exc_info = sys.exc_info()
    with capture_internal_exceptions():
        _capture_exception(exc_info, hub)
    reraise(*exc_info)

import pyspark.daemon as original_daemon

if __name__ == '__main__':
    try:
        original_daemon.manager()
    except Exception as e:
        raise_exception(client)
