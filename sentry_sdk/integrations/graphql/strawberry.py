import importlib.metadata

from sentry_sdk._types import TYPE_CHECKING
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.hub import Hub
from sentry_sdk.utils import logger, parse_version

try:
    from strawberry import Schema
    from strawberry.extensions import SchemaExtension
    from strawberry.extensions.tracing.utils import should_skip_tracing
    from strawberry.utils.cached_property import cached_property
    from strawberry.extensions.tracing import (
        SentryTracingExtension as StrawberrySentryAsyncExtension,
        SentryTracingExtensionSync as StrawberrySentrySyncExtension,
    )
except ImportError:
    raise DidNotEnable("strawberry-graphql is not installed.")

import hashlib
from inspect import isawaitable
from typing import Any, Callable, Generator, Optional

from sentry_sdk import configure_scope, start_span


if TYPE_CHECKING:
    from graphql import GraphQLResolveInfo
    from strawberry.types.execution import ExecutionContext


class StrawberryIntegration(Integration):
    identifier = "strawberry"

    @staticmethod
    def setup_once():
        # type: () -> None

        installed_packages = _get_installed_modules()
        version = parse_version(installed_packages["strawberry-graphql"])

        if version is None:
            raise DidNotEnable(
                "Unparsable strawberry-graphql version: {}".format(version)
            )

        if version < (0, 208):
            raise DidNotEnable("strawberry-graphql 0.208 or newer required.")
