"""This package"""
from threading import Lock
from collections import namedtuple

from sentry_sdk.hub import Hub
from sentry_sdk._compat import with_metaclass, iteritems


_installer_lock = Lock()
_installed_integrations = set()


def iter_default_integrations():
    """Returns an iterator of default integration classes.

    This returns the following default integration:

    - `LoggingIntegration`
    - `ExcepthookIntegration`
    - `DedupeIntegration`
    - `AtexitIntegration`
    """
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.stdlib import StdlibIntegration
    from sentry_sdk.integrations.excepthook import ExcepthookIntegration
    from sentry_sdk.integrations.dedupe import DedupeIntegration
    from sentry_sdk.integrations.atexit import AtexitIntegration
    from sentry_sdk.integrations.modules import ModulesIntegration

    yield LoggingIntegration
    yield StdlibIntegration
    yield ExcepthookIntegration
    yield DedupeIntegration
    yield AtexitIntegration
    yield ModulesIntegration


def setup_integrations(integrations, with_defaults=True):
    """Given a list of integration instances this installs them all.  When
    `with_defaults` is set to `True` then all default integrations are added
    unless they were already provided before.
    """
    integrations = dict(
        (integration.identifier, integration) for integration in integrations or ()
    )

    if with_defaults:
        for integration_cls in iter_default_integrations():
            if integration_cls.identifier not in integrations:
                instance = integration_cls()
                integrations[instance.identifier] = instance

    for identifier, integration in iteritems(integrations):
        with _installer_lock:
            if identifier not in _installed_integrations:
                type(integration).install()
                _installed_integrations.add(identifier)

    return integrations


IntegrationAttachment = namedtuple(
    "IntegrationAttachment", ["integration", "client", "hub"]
)


class IntegrationMeta(type):
    identifier = None
    """A unique identifying string for the integration. Integrations must set
    this as a class attribute.
    """

    def install(cls):
        """
        Initialize the integration.

        This function is only called once, ever. Configuration is not available
        at this point, so the only thing to do here is to hook into exception
        handlers, and perhaps do monkeypatches.

        Inside those hooks `Integration.current` can be used to access the
        instance again.
        """
        raise NotImplementedError()

    @property
    def current_attachment(self):
        """Returns the integration attachment for the current hub."""
        return self.attachment_from_hub(Hub.current)

    @property
    def is_active(self):
        """A faster shortcut for `current_attachment is not None`."""
        hub = Hub.current
        return (
            self.identifier in _installed_integrations
            and hub.client is not None
            and hub.client.integrations.get(self.identifier) is not None
        )

    def attachment_from_hub(self, hub):
        # If an integration is not installed at all we will never return
        # an attachment for it.  This *should not* happen.
        if self.identifier not in _installed_integrations:
            return None

        client = hub.client
        if client is None:
            return

        integration = client.integrations.get(self.identifier, None)
        if integration is None:
            return

        return IntegrationAttachment(hub=hub, integration=integration, client=client)


class Integration(with_metaclass(IntegrationMeta)):
    """Baseclass for all integrations.

    To accept options for an integration, implement your own constructor that
    saves those options on `self`.
    """
