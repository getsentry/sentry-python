"""This package"""
from __future__ import absolute_import

from threading import Lock

from sentry_sdk._compat import iteritems
from sentry_sdk.utils import logger

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Iterator
    from typing import Dict
    from typing import List
    from typing import Set
    from typing import Type
    from typing import Callable


_installer_lock = Lock()
_installed_integrations = set()  # type: Set[str]


def _generate_default_integrations_iterator(*import_strings):
    # type: (*str) -> Callable[[], Iterator[Type[Integration]]]
    def iter_default_integrations():
        # type: () -> Iterator[Type[Integration]]
        """Returns an iterator of the default integration classes:
        """
        from importlib import import_module

        for import_string in import_strings:
            module, cls = import_string.rsplit(".", 1)
            yield getattr(import_module(module), cls)

    if isinstance(iter_default_integrations.__doc__, str):
        for import_string in import_strings:
            iter_default_integrations.__doc__ += "\n- `{}`".format(import_string)

    return iter_default_integrations


iter_default_integrations = _generate_default_integrations_iterator(
    "sentry_sdk.integrations.logging.LoggingIntegration",
    "sentry_sdk.integrations.stdlib.StdlibIntegration",
    "sentry_sdk.integrations.excepthook.ExcepthookIntegration",
    "sentry_sdk.integrations.dedupe.DedupeIntegration",
    "sentry_sdk.integrations.atexit.AtexitIntegration",
    "sentry_sdk.integrations.modules.ModulesIntegration",
    "sentry_sdk.integrations.argv.ArgvIntegration",
    "sentry_sdk.integrations.threading.ThreadingIntegration",
)

del _generate_default_integrations_iterator


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

    for identifier, integration in iteritems(integrations):  # type: ignore
        with _installer_lock:
            if identifier not in _installed_integrations:
                logger.debug(
                    "Setting up previously not enabled integration %s", identifier
                )
                try:
                    type(integration).setup_once()
                except NotImplementedError:
                    if getattr(integration, "install", None) is not None:
                        logger.warning(
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
        # type: () -> None
        """
        Initialize the integration.

        This function is only called once, ever. Configuration is not available
        at this point, so the only thing to do here is to hook into exception
        handlers, and perhaps do monkeypatches.

        Inside those hooks `Integration.current` can be used to access the
        instance again.
        """
        raise NotImplementedError()
