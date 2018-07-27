from __future__ import print_function

import sys
from threading import Lock


_installer_lock = Lock()
_installed_integrations = {}


class Integration(object):
    identifier = None

    def __init__(self, **kwargs):
        """Initialize an integration."""
        raise NotImplementedError()

    def install(self, client):
        raise NotImplementedError()

    def __call__(self, client):
        assert self.identifier
        with _installer_lock:
            if self.identifier in _installed_integrations:
                print(
                    "warning: %s integration for Sentry is already "
                    "configured. Will ignore second configuration." % self.identifier,
                    file=sys.stderr,
                )
                return

            self.install(client)
            _installed_integrations[self.identifier] = self
