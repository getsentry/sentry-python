import importlib.util
import sys
from abc import ABC, abstractmethod
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from itertools import zip_longest
from threading import Lock, local
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.utils import capture_internal_exceptions, logger

if TYPE_CHECKING:
    from collections.abc import Container, Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType
    from typing import (
        Any,
        Callable,
        Dict,
        Iterator,
        List,
        Optional,
        Set,
        Tuple,
        Type,
        Union,
    )


_DEFAULT_FAILED_REQUEST_STATUS_CODES = frozenset(range(500, 600))


_installer_lock = Lock()

# Set of all integration identifiers we have attempted to install
_processed_integrations: "Set[str]" = set()

# Set of all integration identifiers we have actually installed
_installed_integrations: "Set[str]" = set()

# Trigger module -> (integration import string, client options) of deferred
# integrations waiting for the application to import the trigger module
_pending_deferred_integrations: "Dict[str, Tuple[str, Optional[Dict[str, Any]]]]" = {}

_deferred_setup_finder_guard = local()


def _generate_default_integrations_iterator(
    integrations: "List[str]",
    auto_enabling_integrations: "List[str]",
) -> "Callable[..., Iterator[Type[Integration]]]":
    def iter_default_integrations(
        with_auto_enabling_integrations: bool,
        exclude: "Container[str]" = (),
    ) -> "Iterator[Type[Integration]]":
        """Returns an iterator of the default integration classes:"""
        if with_auto_enabling_integrations:
            all_import_strings = integrations + auto_enabling_integrations
        else:
            all_import_strings = integrations

        for import_string in all_import_strings:
            if import_string in exclude:
                continue
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
    "sentry_sdk.integrations.google_genai.GoogleGenAIIntegration",
    "sentry_sdk.integrations.graphene.GrapheneIntegration",
    "sentry_sdk.integrations.httpx.HttpxIntegration",
    "sentry_sdk.integrations.httpx2.Httpx2Integration",
    "sentry_sdk.integrations.huey.HueyIntegration",
    "sentry_sdk.integrations.huggingface_hub.HuggingfaceHubIntegration",
    "sentry_sdk.integrations.langchain.LangchainIntegration",
    "sentry_sdk.integrations.langgraph.LanggraphIntegration",
    "sentry_sdk.integrations.litestar.LitestarIntegration",
    "sentry_sdk.integrations.loguru.LoguruIntegration",
    "sentry_sdk.integrations.mcp.MCPIntegration",
    "sentry_sdk.integrations.openai.OpenAIIntegration",
    "sentry_sdk.integrations.openai_agents.OpenAIAgentsIntegration",
    "sentry_sdk.integrations.pydantic_ai.PydanticAIIntegration",
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

# With the `deferred_auto_enabling_integrations` experiment on, these auto-enabling
# integrations are only set up once the application imports the trigger module.
# This can't live on the integration class, since loading the class imports the library.
_DEFERRED_INTEGRATION_TRIGGERS = {
    "sentry_sdk.integrations.anthropic.AnthropicIntegration": "anthropic",
    "sentry_sdk.integrations.google_genai.GoogleGenAIIntegration": "google.genai",
    "sentry_sdk.integrations.huggingface_hub.HuggingfaceHubIntegration": "huggingface_hub.inference._client",
    "sentry_sdk.integrations.openai.OpenAIIntegration": "openai",
    "sentry_sdk.integrations.openai_agents.OpenAIAgentsIntegration": "agents",
    "sentry_sdk.integrations.pydantic_ai.PydanticAIIntegration": "pydantic_ai",
}

iter_default_integrations = _generate_default_integrations_iterator(
    integrations=_DEFAULT_INTEGRATIONS,
    auto_enabling_integrations=_AUTO_ENABLING_INTEGRATIONS,
)

del _generate_default_integrations_iterator


_MIN_VERSIONS = {
    "aiohttp": (3, 4),
    "aiomysql": (0, 3, 0),
    "anthropic": (0, 16),
    "ariadne": (0, 20),
    "arq": (0, 23),
    "asyncpg": (0, 23),
    "beam": (2, 12),
    "boto3": (1, 16),  # botocore
    "bottle": (0, 12),
    "celery": (4, 4, 7),
    "chalice": (1, 16, 0),
    "clickhouse_driver": (0, 2, 0),
    "cohere": (5, 4, 0),
    "django": (1, 8),
    "dramatiq": (1, 9),
    "falcon": (1, 4),
    "fastapi": (0, 79, 0),
    "flask": (1, 1, 4),
    "gql": (3, 4, 1),
    "graphene": (3, 3),
    "google_genai": (1, 29, 0),  # google-genai
    "grpc": (1, 32, 0),  # grpcio
    "httpx": (0, 16, 0),
    "httpx2": (2, 0, 0),
    "huggingface_hub": (0, 24, 7),
    "langchain": (0, 1, 0),
    "langgraph": (0, 6, 6),
    "launchdarkly": (9, 8, 0),
    "litellm": (1, 77, 5),
    "loguru": (0, 7, 0),
    "mcp": (1, 15, 0),
    "mistral": (2, 0, 5),
    "openai": (1, 0, 0),
    "openai_agents": (0, 0, 19),
    "openfeature": (0, 7, 1),
    "pydantic_ai": (1, 0, 0),
    "pymongo": (3, 5, 0),
    "pyreqwest": (0, 11, 6),
    "quart": (0, 16, 0),
    "ray": (2, 7, 0),
    "requests": (2, 0, 0),
    "rq": (0, 6),
    "sanic": (0, 8),
    "sqlalchemy": (1, 2),
    "starlette": (0, 16),
    "starlite": (1, 48),
    "statsig": (0, 55, 3),
    "strawberry": (0, 209, 5),
    "tornado": (6, 0),
    "typer": (0, 15),
    "typesafe": (0, 7),
    "unleash": (6, 0, 1),
}


_INTEGRATION_DEACTIVATES = {
    "langchain": {"openai", "anthropic", "google_genai"},
    "openai_agents": {"openai"},
    "pydantic_ai": {"openai", "anthropic"},
}


def setup_integrations(
    integrations: "Sequence[Integration]",
    with_defaults: bool = True,
    with_auto_enabling_integrations: bool = False,
    disabled_integrations: "Optional[Sequence[Union[type[Integration], Integration]]]" = None,
    options: "Optional[Dict[str, Any]]" = None,
) -> "Dict[str, Integration]":
    """
    Given a list of integration instances, this installs them all.

    When `with_defaults` is set to `True` all default integrations are added
    unless they were already provided before.

    `disabled_integrations` takes precedence over `with_defaults` and
    `with_auto_enabling_integrations`.

    Some integrations are designed to automatically deactivate other integrations
    in order to avoid conflicts and prevent duplicate telemetry from being collected.
    For example, enabling the `langchain` integration will auto-deactivate both the
    `openai` and `anthropic` integrations.

    Users can override this behavior by:
      - Explicitly providing an integration in the `integrations=[]` list, or
      - Disabling the higher-level integration via the `disabled_integrations` option.
    """
    integrations = dict(
        (integration.identifier, integration) for integration in integrations or ()
    )

    logger.debug("Setting up integrations (with default = %s)", with_defaults)

    deferred: "Dict[str, Optional[str]]" = {}
    if (
        with_defaults
        and with_auto_enabling_integrations
        and options is not None
        and options["_experiments"].get("deferred_auto_enabling_integrations")
    ):
        for import_string, trigger in _DEFERRED_INTEGRATION_TRIGGERS.items():
            if trigger in sys.modules:
                continue
            try:
                spec = importlib.util.find_spec(trigger)
            except ImportError:
                spec = None
            if spec is None:
                deferred[import_string] = None
            elif hasattr(spec.loader, "exec_module"):
                deferred[import_string] = trigger

    user_provided_integrations = set(integrations.keys())

    # Integrations that will not be enabled
    disabled_integrations = [
        integration if isinstance(integration, type) else type(integration)
        for integration in disabled_integrations or []
    ]

    # Integrations that are not explicitly set up by the user.
    used_as_default_integration = set()

    if with_defaults:
        for integration_cls in iter_default_integrations(
            with_auto_enabling_integrations, exclude=deferred
        ):
            if integration_cls.identifier not in integrations:
                instance = integration_cls()
                integrations[instance.identifier] = instance
                used_as_default_integration.add(instance.identifier)

    disabled_integration_identifiers = {
        integration.identifier for integration in disabled_integrations
    }
    deactivated_identifiers = set()

    # A deferred integration can't be set up yet, so it deactivates its targets
    # whenever its library is installed, even if it later turns out too old to enable.
    installed_deferred_identifiers = {
        import_string.rsplit(".", 2)[1]
        for import_string, trigger in deferred.items()
        if trigger is not None
    }

    for integration, targets_to_deactivate in _INTEGRATION_DEACTIVATES.items():
        if (
            integration in integrations or integration in installed_deferred_identifiers
        ) and integration not in disabled_integration_identifiers:
            for target in targets_to_deactivate:
                if target not in user_provided_integrations:
                    deactivated_identifiers.add(target)
                    for cls in iter_default_integrations(True, exclude=deferred):
                        if cls.identifier == target:
                            if cls not in disabled_integrations:
                                disabled_integrations.append(cls)
                                logger.debug(
                                    "Auto-deactivating %s integration because %s integration is active",
                                    target,
                                    integration,
                                )

    for identifier, integration in integrations.items():
        with _installer_lock:
            if identifier not in _processed_integrations:
                if type(integration) in disabled_integrations:
                    logger.debug("Ignoring integration %s", identifier)
                else:
                    logger.debug(
                        "Setting up previously not enabled integration %s", identifier
                    )
                    try:
                        type(integration).setup_once()
                        integration.setup_once_with_options(options)
                    except DidNotEnable as e:
                        if identifier not in used_as_default_integration:
                            raise

                        logger.debug(
                            "Did not enable default integration %s: %s", identifier, e
                        )
                    else:
                        _installed_integrations.add(identifier)

                _processed_integrations.add(identifier)

    for import_string, trigger in deferred.items():
        identifier = import_string.rsplit(".", 2)[1]
        if (
            trigger is None
            or identifier in disabled_integration_identifiers
            or identifier in deactivated_identifiers
        ):
            continue

        with _installer_lock:
            if identifier in _processed_integrations:
                continue
            _processed_integrations.add(identifier)
            _pending_deferred_integrations[trigger] = (import_string, options)
            if _deferred_setup_finder not in sys.meta_path:
                sys.meta_path.insert(0, _deferred_setup_finder)

        logger.debug(
            "Deferring setup of integration %s until %s is imported",
            identifier,
            trigger,
        )

    integrations = {
        identifier: integration
        for identifier, integration in integrations.items()
        if identifier in _installed_integrations
    }

    for identifier in integrations:
        logger.debug("Enabling integration %s", identifier)

    return integrations


def _set_up_deferred_integration(trigger: str) -> None:
    with _installer_lock:
        pending = _pending_deferred_integrations.pop(trigger, None)
        if (
            not _pending_deferred_integrations
            and _deferred_setup_finder in sys.meta_path
        ):
            sys.meta_path.remove(_deferred_setup_finder)

    if pending is None:
        return

    import_string, options = pending
    with capture_internal_exceptions():
        module, cls = import_string.rsplit(".", 1)
        try:
            integration = getattr(import_module(module), cls)()
            type(integration).setup_once()
            integration.setup_once_with_options(options)
        except DidNotEnable as e:
            logger.debug("Did not enable default integration %s: %s", import_string, e)
            return

        _installed_integrations.add(integration.identifier)
        sentry_sdk.get_client().integrations[integration.identifier] = integration
        logger.debug("Enabling integration %s", integration.identifier)


class _DeferredSetupLoader(Loader):
    def __init__(self, loader: "Any", trigger: str) -> None:
        self._loader = loader
        self._trigger = trigger

    def create_module(self, spec: "ModuleSpec") -> "Optional[ModuleType]":
        return self._loader.create_module(spec)

    def exec_module(self, module: "ModuleType") -> None:
        self._loader.exec_module(module)
        module.__loader__ = self._loader
        if module.__spec__ is not None:
            module.__spec__.loader = self._loader

        # The import system only binds a submodule to its parent after exec_module
        # returns, but setup may already access it as an attribute of the parent.
        parent, _, child = self._trigger.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, sys.modules.get(self._trigger, module))

        _set_up_deferred_integration(self._trigger)

    def __getattr__(self, name: str) -> "Any":
        return getattr(self._loader, name)


class _DeferredSetupFinder(MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: "Optional[Sequence[str]]" = None,
        target: "Optional[ModuleType]" = None,
    ) -> "Optional[ModuleSpec]":
        if fullname not in _pending_deferred_integrations or getattr(
            _deferred_setup_finder_guard, "active", False
        ):
            return None

        _deferred_setup_finder_guard.active = True
        try:
            for finder in sys.meta_path:
                if finder is self or not hasattr(finder, "find_spec"):
                    continue
                spec = finder.find_spec(fullname, path, target)
                if spec is not None:
                    break
            else:
                return None
        finally:
            _deferred_setup_finder_guard.active = False

        if hasattr(spec.loader, "exec_module"):
            spec.loader = _DeferredSetupLoader(spec.loader, fullname)
        return spec


_deferred_setup_finder = _DeferredSetupFinder()


def _check_minimum_version(
    integration: "type[Integration]",
    version: "Optional[tuple[int, ...]]",
    package: "Optional[str]" = None,
) -> None:
    package = package or integration.identifier

    if version is None:
        raise DidNotEnable(f"Unparsable {package} version.")

    min_version = _MIN_VERSIONS.get(integration.identifier)
    if min_version is None:
        return

    # We can't use normal tuple comparison here because the version tuples might
    # not have the same length, in which case they wouldn't compare as expected.
    for v, min in zip_longest(version, min_version, fillvalue=0):
        if v == min:
            continue
        elif v < min:
            raise DidNotEnable(
                f"Integration only supports {package} {'.'.join(map(str, min_version))} or newer."
            )
        elif v > min:
            return


class DidNotEnable(Exception):
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

    identifier: "str" = None  # type: ignore[assignment]
    """String unique ID of integration type"""

    @staticmethod
    @abstractmethod
    def setup_once() -> None:
        """
        Initialize the integration.

        This function is only called once, ever. Configuration is not available
        at this point, so the only thing to do here is to hook into exception
        handlers, and perhaps do monkeypatches.

        Inside those hooks `Integration.current` can be used to access the
        instance again.
        """
        pass

    def setup_once_with_options(
        self, options: "Optional[Dict[str, Any]]" = None
    ) -> None:
        """
        Called after setup_once in rare cases on the instance and with options since we don't have those available above.
        """
        pass
