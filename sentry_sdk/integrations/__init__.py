"""This package"""
from threading import Lock

from ..utils import logger
from ..consts import INTEGRATIONS as _installed_integrations


_installer_lock = Lock()


def get_default_integrations():
    """Returns an iterator of default integration instances.

    This returns the following default integration:

    - `LoggingIntegration`
    - `ExcepthookIntegration`
    - `DedupeIntegration`
    - `AtexitIntegration`
    """
    from .logging import LoggingIntegration
    from .stdlib import StdlibIntegration
    from .excepthook import ExcepthookIntegration
    from .dedupe import DedupeIntegration
    from .atexit import AtexitIntegration

    yield LoggingIntegration()
    yield StdlibIntegration()
    yield ExcepthookIntegration()
    yield DedupeIntegration()
    yield AtexitIntegration()


def setup_integrations(integrations, with_defaults=True):
    """Given a list of integration instances this installs them all.  When
    `with_defaults` is set to `True` then all default integrations are added
    unless they were already provided before.
    """
    integrations = list(integrations)
    if with_defaults:
        for instance in get_default_integrations():
            if not any(isinstance(x, type(instance)) for x in integrations):
                integrations.append(instance)

    for integration in integrations:
        integration()


class Integration(object):
    """Baseclass for all integrations."""

    identifier = None
    """A unique identifying string for the integration.  Integrations must
    set this as a class attribute.
    """

    def install(self):
        """An integration must implement all its code here.  When the
        `setup_integrations` function runs it will invoke this unless the
        integration was already activated elsewhere.
        """
        raise NotImplementedError()

    def __call__(self):
        assert self.identifier
        with _installer_lock:
            if self.identifier in _installed_integrations:
                logger.warning(
                    "%s integration for Sentry is already "
                    "configured. Will ignore second configuration.",
                    self.identifier,
                )
                return

            self.install()
            _installed_integrations.append(self.identifier)
