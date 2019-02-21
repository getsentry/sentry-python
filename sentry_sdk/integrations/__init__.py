"""This package"""
from __future__ import absolute_import

from threading import Lock
from collections import namedtuple

from sentry_sdk._compat import iteritems
from sentry_sdk.utils import logger

if False:
    from typing import Iterator
    from typing import Dict
    from typing import List
    from typing import Set
    from typing import Type


_installer_lock = Lock()
_installed_integrations = set()  # type: Set[str]


def iter_default_integrations():
    # type: () -> Iterator[Type[Integration]]
    """Returns an iterator of default integration classes.

    This returns the following default integration:

    - `LoggingIntegration`
    - `StdlibIntegration`
    - `ExcepthookIntegration`
    - `DedupeIntegration`
    - `AtexitIntegration`
    - `ModulesIntegration`
    - `ArgvIntegration`
    """
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.stdlib import StdlibIntegration
    from sentry_sdk.integrations.excepthook import ExcepthookIntegration
    from sentry_sdk.integrations.dedupe import DedupeIntegration
    from sentry_sdk.integrations.atexit import AtexitIntegration
    from sentry_sdk.integrations.modules import ModulesIntegration
    from sentry_sdk.integrations.argv import ArgvIntegration
    from sentry_sdk.integrations.threading import ThreadingIntegration

    yield LoggingIntegration
    yield StdlibIntegration
    yield ExcepthookIntegration
    yield DedupeIntegration
    yield AtexitIntegration
    yield ModulesIntegration
    yield ArgvIntegration
    yield ThreadingIntegration


def setup_integrations(integrations, with_defaults=True):
    # type: (List[Integration], bool) -> Dict[str, Integration]
    """Given a list of integration instances this installs them all.  When
    `with_defaults` is set to `True` then all default integrations are added
    unless they were already provided before.
    """
    integrations = dict(
        (integration.identifier, integration) for integration in integrations or ()
    )

    logger.debug("Setting up integrations (with default = %s)", with_defaults)

    if with_defaults:
        for integration_cls in iter_default_integrations():
            if integration_cls.identifier not in integrations:
                instance = integration_cls()
                integrations[instance.identifier] = instance

    for identifier, integration in iteritems(integrations):
        with _installer_lock:
            if identifier not in _installed_integrations:
                logger.debug(
                    "Setting up previously not enabled integration %s", identifier
                )
                try:
                    type(integration).setup_once()
                except NotImplementedError:
                    if getattr(integration, "install", None) is not None:
                        logger.warn(
                            "Integration %s: The install method is "
                            "deprecated. Use `setup_once`.",
                            identifier,
                        )
                        integration.install()
                    else:
                        raise
                _installed_integrations.add(identifier)

    for identifier in integrations:
        logger.debug("Enabling integration %s", identifier)

    return integrations


IntegrationAttachment = namedtuple(
    "IntegrationAttachment", ["integration", "client", "hub"]
)


class Integration(object):
    """Baseclass for all integrations.

    To accept options for an integration, implement your own constructor that
    saves those options on `self`.
    """

    install = None
    """Legacy method, do not implement."""

    identifier = None  # type: str
    """String unique ID of integration type"""

    @staticmethod
    def setup_once():
        """
        Initialize the integration.

        This function is only called once, ever. Configuration is not available
        at this point, so the only thing to do here is to hook into exception
        handlers, and perhaps do monkeypatches.

        Inside those hooks `Integration.current` can be used to access the
        instance again.
        """
        raise NotImplementedError()
