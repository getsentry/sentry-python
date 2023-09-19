from sentry_sdk import configure_scope, start_span
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.hub import Hub
from sentry_sdk.utils import logger, parse_version
from sentry_sdk._types import TYPE_CHECKING

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
    raise DidNotEnable("strawberry-graphql is not installed")

import hashlib
from inspect import isawaitable
from typing import Any, Callable, Generator, Optional


if TYPE_CHECKING:
    from graphql import GraphQLResolveInfo
    from strawberry.types.execution import ExecutionContext


class StrawberryIntegration(Integration):
    identifier = "strawberry"

    def __init__(self, async_execution=None):
        # type: (Optional[bool]) -> None
        if async_execution not in (None, False, True):
            raise ValueError(
                "Invalid value for async_execution: %s (must be bool)"
                % (async_execution)
            )
        self.async_execution = async_execution

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

        patch_schema_init()


def patch_schema_init():
    # type: () -> None
    old_schema = Schema.__init__

    def _sentry_patched_schema_init(self, *args, **kwargs):
        integration = Hub.current.get_integration(StrawberryIntegration)
        if integration is None:
            return old_schema(self, *args, **kwargs)

        extensions = kwargs.get("extensions") or []

        if integration.async_execution is not None:
            should_use_async_extension = integration.async_execution
        else:
            # try to figure it out ourselves
            should_use_async_extension = bool(
                {"starlette", "starlite", "litestar", "fastapi"}
                & set(_get_installed_modules())
            )

            logger.info(
                "Assuming strawberry is running in %s context. If not, initialize the integration with async_execution=%s.",
                "async" if should_use_async_extension else "sync",
                "False" if should_use_async_extension else "True",
            )

        # remove the strawberry sentry extension, if present, to avoid double
        # tracing
        extensions = [
            extension
            for extension in extensions
            if extension
            not in (StrawberrySentryAsyncExtension, StrawberrySentrySyncExtension)
        ]

        # add our extension
        extensions.append(
            SentryAsyncExtension if should_use_async_extension else SentrySyncExtension
        )

        kwargs["extensions"] = extensions

        return old_schema(self, *args, **kwargs)

    # XXX
    not_yet_patched = old_schema.__name__ != "_sentry_patched_schema_init"
    if not_yet_patched:
        Schema.__init__ = _sentry_patched_schema_init


class SentryAsyncExtension(SchemaExtension):
    def __init__(
        self,
        *,
        execution_context=None,  # type: Optional[ExecutionContext]
    ):
        if execution_context:
            self.execution_context = execution_context

    @cached_property
    def _resource_name(self):
        assert self.execution_context.query

        query_hash = self.hash_query(self.execution_context.query)

        if self.execution_context.operation_name:
            return "{}:{}".format(self.execution_context.operation_name, query_hash)

        return query_hash

    def hash_query(self, query):
        # type: (str) -> str
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    def on_operation(self):
        # type: () -> Generator[None, None, None]
        self._operation_name = self.execution_context.operation_name
        name = self._operation_name if self._operation_name else "anonymous query"

        self.operation_type = "query"

        assert self.execution_context.query

        if self.execution_context.query.strip().startswith("mutation"):
            self.operation_type = "mutation"
        if self.execution_context.query.strip().startswith("subscription"):
            self.operation_type = "subscription"

        with configure_scope() as scope:
            if scope.span:
                self.graphql_span = scope.span.start_child(
                    op="graphlql.{}".format(self.operation_type),
                    description=name,
                )
            else:
                self.graphql_span = start_span(
                    op="graphql.{}".format(self.operation_type),
                    description=name,
                )

        self.graphql_span.set_tag("graphql.operation_type", self.operation_type)
        self.graphql_span.set_tag("graphql.resource_name", self._resource_name)
        self.graphql_span.set_data("graphql.query", self.execution_context.query)

        yield

        self.graphql_span.finish()

    def on_validate(self):
        # type: () -> Generator[None, None, None]
        validation_span = self.graphql_span.start_child(
            op="graphql.validate", description="validation"
        )

        yield

        validation_span.finish()

    def on_parse(self):
        # type: () -> Generator[None, None, None]
        parsing_span = self.graphql_span.start_child(
            op="graphql.parse", description="parsing"
        )

        yield

        parsing_span.finish()

    def should_skip_tracing(self, _next, info):
        # type: (Callable, GraphQLResolveInfo) -> bool
        return should_skip_tracing(_next, info)

    async def resolve(
        self,
        _next,  # type: Callable
        root,  # type: Any
        info,  # type: GraphQLResolveInfo
        *args,  # type: str
        **kwargs,  # type: Any
    ):
        # type: (...) -> Any
        if self.should_skip_tracing(_next, info):
            result = _next(root, info, *args, **kwargs)

            if isawaitable(result):
                result = await result

            return result

        field_path = f"{info.parent_type}.{info.field_name}"

        with self.graphql_span.start_child(
            op="graphql.resolve", description="resolving: {}".format(field_path)
        ) as span:
            span.set_tag("graphql.field_name", info.field_name)
            span.set_tag("graphql.parent_type", info.parent_type.name)
            span.set_tag("graphql.field_path", field_path)
            span.set_tag("graphql.path", ".".join(map(str, info.path.as_list())))

            result = _next(root, info, *args, **kwargs)

            if isawaitable(result):
                result = await result

            return result


class SentrySyncExtension(SentryAsyncExtension):
    def resolve(
        self,
        _next,  # type: Callable
        root,  # type: Any
        info,  # type: GraphQLResolveInfo
        *args,  # type: str
        **kwargs,  # type: Any
    ):
        # type: (...) -> Any
        if self.should_skip_tracing(_next, info):
            result = _next(root, info, *args, **kwargs)
            return result

        field_path = f"{info.parent_type}.{info.field_name}"

        with self.graphql_span.start_child(
            op="graphql.resolve", description="resolving: {}".format(field_path)
        ) as span:
            span.set_tag("graphql.field_name", info.field_name)
            span.set_tag("graphql.parent_type", info.parent_type.name)
            span.set_tag("graphql.field_path", field_path)
            span.set_tag("graphql.path", ".".join(map(str, info.path.as_list())))

            result = _next(root, info, *args, **kwargs)

            return result
