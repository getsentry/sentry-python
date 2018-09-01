from threading import Lock

from ..utils import logger


_installer_lock = Lock()
_installed_integrations = {}


def _get_default_integrations():
    from .logging import LoggingIntegration
    from .excepthook import ExcepthookIntegration
    from .dedupe import DedupeIntegration
    from .atexit import AtexitIntegration

    yield LoggingIntegration
    yield ExcepthookIntegration
    yield DedupeIntegration
    yield AtexitIntegration


def setup_integrations(options):
    integrations = list(options.get("integrations", None) or ())
    default_integrations = options.get("default_integrations") or False

    if default_integrations:
        for cls in _get_default_integrations():
            if not any(isinstance(x, cls) for x in integrations):
                integrations.append(cls())

    for integration in integrations:
        integration()


class Integration(object):
    identifier = None

    def install(self):
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
            _installed_integrations[self.identifier] = self
