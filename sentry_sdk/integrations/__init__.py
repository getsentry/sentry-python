"""This package"""
from threading import Lock

from sentry_sdk.hub import Hub
from sentry_sdk._compat import with_metaclass
from sentry_sdk.consts import INTEGRATIONS as _installed_integrations


_installer_lock = Lock()


def get_default_integrations():
    """Returns an iterator of default integration instances.

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

    yield LoggingIntegration()
    yield StdlibIntegration()
    yield ExcepthookIntegration()
    yield DedupeIntegration()
    yield AtexitIntegration()
    yield ModulesIntegration()


def setup_integrations(integrations, with_defaults=True):
    """Given a list of integration instances this installs them all.  When
    `with_defaults` is set to `True` then all default integrations are added
    unless they were already provided before.
    """
    integrations = dict(
        (integration.identifier, integration) for integration in integrations or ()
    )

    if with_defaults:
        for instance in get_default_integrations():
            integrations.setdefault(instance.identifier, instance)

    for identifier, integration in integrations.items():
        assert identifier
        assert identifier == integration.identifier

        with _installer_lock:
            if identifier in _installed_integrations:
                continue

            # Make sure the integration has defined `install` as classmethod
            type(integration).install()
            _installed_integrations.append(identifier)

    return integrations


class IntegrationMeta(type):
    identifier = None
    """A unique identifying string for the integration. Integrations must set
    this as a class attribute.
    """

    @property
    def current(self):
        """
        Return the integration instance that was passed to the current hub's
        (`Hub.current`) client.

        This is only available after `Integration.install` has returned.

        If this returns `None` this means that the integration is not enabled
        for this client and it should not do anything.
        """

        return self._get_instance_from_hub(Hub.current)

    @property
    def main(self):
        """
        This is the same as `Integration.current` except that it uses
        `Hub.main` instead of `Hub.current`. This is useful in certain (rare)
        situations where an integration is only applicable to the main Hub's
        client.
        """
        return self._get_instance_from_hub(Hub.main)

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

    def _get_instance_from_hub(self, hub):
        if self.identifier not in _installed_integrations:
            raise AssertionError("Access options on-demand, not in install()")

        hub = Hub.current
        if hub is None:
            return
        client = hub.client
        if client is None:
            return

        return client.options["integrations"].get(self.identifier, None)


class Integration(with_metaclass(IntegrationMeta)):
    """Baseclass for all integrations.

    To accept options for an integration, implement your own constructor that
    saves those options on `self`.
    """
