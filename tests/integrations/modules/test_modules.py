import re
import sentry_sdk

from sentry_sdk.integrations.modules import (
    ModulesIntegration,
    _get_installed_modules,
)


def _normalize_distribution_name(name):
    # type: (str) -> str
    """Normalize distribution name according to PEP-0503.

    See:
    https://peps.python.org/pep-0503/#normalized-names
    for more details.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())

    (event,) = events
    assert "sentry-sdk" in event["modules"]
    assert "pytest" in event["modules"]


def test_installed_modules():
    try:
        from importlib import distributions, version

        importlib_available = True
    except ImportError:
        importlib_available = False

    try:
        import pkg_resources

        pkg_resources_available = True
    except ImportError:
        pkg_resources_available = False

    installed_distributions = {
        _normalize_distribution_name(dist): version
        for dist, version in _get_installed_modules().items()
    }

    if importlib_available:
        importlib_distributions = {
            _normalize_distribution_name(dist.metadata["Name"]): version(
                dist.metadata["Name"]
            )
            for dist in distributions()
        }
        assert installed_distributions == importlib_distributions

    if pkg_resources_available:
        pkg_resources_distributions = {
            _normalize_distribution_name(dist.key): dist.version
            for dist in pkg_resources.working_set
        }
        assert installed_distributions == pkg_resources_distributions
