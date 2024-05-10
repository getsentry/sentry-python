from abc import ABC, abstractmethod
from threading import Lock

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.utils import logger


if TYPE_CHECKING:
    from typing import Callable
    from typing import Dict
    from typing import Iterator
    from typing import List
    from typing import Set
    from typing import Type


_installer_lock = Lock()

# Set of all integration identifiers we have attempted to install
_processed_integrations = set()  # type: Set[str]

# Set of all integration identifiers we have actually installed
_installed_integrations = set()  # type: Set[str]


def _generate_default_integrations_iterator(
    integrations,  # type: List[str]
    auto_enabling_integrations,  # type: List[str]
):
    # type: (...) -> Callable[[bool], Iterator[Type[Integration]]]

    def iter_default_integrations(with_auto_enabling_integrations):
        # type: (bool) -> Iterator[Type[Integration]]
        """Returns an iterator of the default integration classes:"""
        from importlib import import_module

        if with_auto_enabling_integrations:
            all_import_strings = integrations + auto_enabling_integrations
        else:
            all_import_strings = integrations

        for import_string in all_import_strings:
            try:
                module, cls = import_string.rsplit(".", 1)
                yield getattr(import_module(module), cls)
            except (DidNotEnable, SyntaxError) as e:
                logger.debug(
                    "Did not import default integration %s: %s", import_string, e
                )

    if isinstance(iter_default_integrations.__doc__, str):
        for import_string in integrations:
            iter_default_integrations.__doc__ += "\n- `{}`".format(import_string)

    return iter_default_integrations


_DEFAULT_INTEGRATIONS = [
    # stdlib/base runtime integrations
    "sentry_sdk.integrations.argv.ArgvIntegration",
    "sentry_sdk.integrations.atexit.AtexitIntegration",
    "sentry_sdk.integrations.dedupe.DedupeIntegration",
    "sentry_sdk.integrations.excepthook.ExcepthookIntegration",
    "sentry_sdk.integrations.logging.LoggingIntegration",
    "sentry_sdk.integrations.modules.ModulesIntegration",
    "sentry_sdk.integrations.stdlib.StdlibIntegration",
    "sentry_sdk.integrations.threading.ThreadingIntegration",
]

_AUTO_ENABLING_INTEGRATIONS = [
    "sentry_sdk.integrations.aiohttp.AioHttpIntegration",
    "sentry_sdk.integrations.anthropic.AnthropicIntegration",
    "sentry_sdk.integrations.ariadne.AriadneIntegration",
    "sentry_sdk.integrations.arq.ArqIntegration",
    "sentry_sdk.integrations.asyncpg.AsyncPGIntegration",
    "sentry_sdk.integrations.boto3.Boto3Integration",
    "sentry_sdk.integrations.bottle.BottleIntegration",
    "sentry_sdk.integrations.celery.CeleryIntegration",
    "sentry_sdk.integrations.chalice.ChaliceIntegration",
    "sentry_sdk.integrations.clickhouse_driver.ClickhouseDriverIntegration",
    "sentry_sdk.integrations.cohere.CohereIntegration",
    "sentry_sdk.integrations.django.DjangoIntegration",
    "sentry_sdk.integrations.falcon.FalconIntegration",
    "sentry_sdk.integrations.fastapi.FastApiIntegration",
    "sentry_sdk.integrations.flask.FlaskIntegration",
    "sentry_sdk.integrations.gql.GQLIntegration",
    "sentry_sdk.integrations.graphene.GrapheneIntegration",
    "sentry_sdk.integrations.httpx.HttpxIntegration",
    "sentry_sdk.integrations.huey.HueyIntegration",
    "sentry_sdk.integrations.huggingface_hub.HuggingfaceHubIntegration",
    "sentry_sdk.integrations.langchain.LangchainIntegration",
    "sentry_sdk.integrations.loguru.LoguruIntegration",
    "sentry_sdk.integrations.openai.OpenAIIntegration",
    "sentry_sdk.integrations.pymongo.PyMongoIntegration",
    "sentry_sdk.integrations.pyramid.PyramidIntegration",
    "sentry_sdk.integrations.quart.QuartIntegration",
    "sentry_sdk.integrations.redis.RedisIntegration",
    "sentry_sdk.integrations.rq.RqIntegration",
    "sentry_sdk.integrations.sanic.SanicIntegration",
    "sentry_sdk.integrations.sqlalchemy.SqlalchemyIntegration",
    "sentry_sdk.integrations.starlette.StarletteIntegration",
    "sentry_sdk.integrations.starlite.StarliteIntegration",
    "sentry_sdk.integrations.strawberry.StrawberryIntegration",
    "sentry_sdk.integrations.tornado.TornadoIntegration",
]


iter_default_integrations = _generate_default_integrations_iterator(
    integrations=_DEFAULT_INTEGRATIONS,
    auto_enabling_integrations=_AUTO_ENABLING_INTEGRATIONS,
)

del _generate_default_integrations_iterator


def setup_integrations(
    integrations, with_defaults=True, with_auto_enabling_integrations=False
):
    # type: (List[Integration], bool, bool) -> Dict[str, Integration]
    """
    Given a list of integration instances, this installs them all.

    When `with_defaults` is set to `True` all default integrations are added
    unless they were already provided before.
    """
    integrations = dict(
        (integration.identifier, integration) for integration in integrations or ()
    )

    logger.debug("Setting up integrations (with default = %s)", with_defaults)

    # Integrations that are not explicitly set up by the user.
    used_as_default_integration = set()

    if with_defaults:
        for integration_cls in iter_default_integrations(
            with_auto_enabling_integrations
        ):
            if integration_cls.identifier not in integrations:
                instance = integration_cls()
                integrations[instance.identifier] = instance
                used_as_default_integration.add(instance.identifier)

    for identifier, integration in integrations.items():
        with _installer_lock:
            if identifier not in _processed_integrations:
                logger.debug(
                    "Setting up previously not enabled integration %s", identifier
                )
                try:
                    type(integration).setup_once()
                except DidNotEnable as e:
                    if identifier not in used_as_default_integration:
                        raise

                    logger.debug(
                        "Did not enable default integration %s: %s", identifier, e
                    )
                else:
                    _installed_integrations.add(identifier)

                _processed_integrations.add(identifier)

    integrations = {
        identifier: integration
        for identifier, integration in integrations.items()
        if identifier in _installed_integrations
    }

    for identifier in integrations:
        logger.debug("Enabling integration %s", identifier)

    return integrations


class DidNotEnable(Exception):  # noqa: N818
    """
    The integration could not be enabled due to a trivial user error like
    `flask` not being installed for the `FlaskIntegration`.

    This exception is silently swallowed for default integrations, but reraised
    for explicitly enabled integrations.
    """


class Integration(ABC):
    """Baseclass for all integrations.

    To accept options for an integration, implement your own constructor that
    saves those options on `self`.
    """

    install = None
    """Legacy method, do not implement."""

    identifier = None  # type: str
    """String unique ID of integration type"""

    @staticmethod
    @abstractmethod
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
        pass
