from __future__ import absolute_import

import os
import sys
import atexit

from sentry_sdk.hub import Hub
from sentry_sdk.utils import logger
from . import Integration


def default_shutdown_callback(pending, timeout):
    def echo(msg):
        sys.stderr.write(msg + "\n")

    echo("Sentry is attempting to send %i pending error messages" % pending)
    echo("Waiting up to %s seconds" % timeout)
    echo("Press Ctrl-%s to quit" % (os.name == "nt" and "Break" or "C"))
    sys.stderr.flush()


class AtexitIntegration(Integration):
    identifier = "atexit"

    def __init__(self, callback=None):
        if callback is None:
            callback = default_shutdown_callback
        self.callback = callback

    def install(self):
        @atexit.register
        def _shutdown():
            main_client = Hub.main.client
            logger.debug("atexit: got shutdown signal")
            if main_client is not None:
                logger.debug("atexit: shutting down client")
                main_client.close(shutdown_callback=self.callback)
