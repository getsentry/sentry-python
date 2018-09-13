from __future__ import absolute_import

import os
import sys
import atexit

from sentry_sdk.hub import Hub
from sentry_sdk.utils import logger
from sentry_sdk.integrations import Integration


def default_shutdown_callback(pending, timeout):
    """This is the default shutdown callback that is set on the options.
    It prints out a message to stderr that informs the user that some events
    are still pending and the process is waiting for them to flush out.
    """

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
