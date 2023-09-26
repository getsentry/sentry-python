import hashlib
from functools import cached_property
from inspect import isawaitable
from sentry_sdk import configure_scope, start_span
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.integrations.modules import _get_installed_modules
from sentry_sdk.hub import Hub, _should_send_default_pii
from sentry_sdk.utils import (
    capture_internal_exceptions,
    event_from_exception,
    logger,
    parse_version,
)
from sentry_sdk._types import TYPE_CHECKING

try:
    import strawberry.http as strawberry_http
    import strawberry.schema.schema as strawberry_schema
    from strawberry import Schema
    from strawberry.extensions import SchemaExtension
    from strawberry.extensions.tracing.utils import should_skip_tracing
    from strawberry.extensions.tracing import (
        SentryTracingExtension as StrawberrySentryAsyncExtension,
        SentryTracingExtensionSync as StrawberrySentrySyncExtension,
    )
    from strawberry.fastapi import router as fastapi_router
    from strawberry.http import async_base_view, sync_base_view
except ImportError:
    raise DidNotEnable("strawberry-graphql is not installed")

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Generator, Optional
    from graphql import GraphQLResolveInfo
    from strawberry.types.execution import ExecutionContext
    from sentry_sdk._types import EventProcessor


ignore_logger("strawberry.execution")


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

        _patch_schema_init()
        _patch_execute()
        _patch_process_result()


def _patch_schema_init():
    # type: () -> None
    old_schema_init = Schema.__init__

    def _sentry_patched_schema_init(self, *args, **kwargs):
        integration = Hub.current.get_integration(StrawberryIntegration)
        if integration is None:
            return old_schema_init(self, *args, **kwargs)

        extensions = kwargs.get("extensions") or []

        if integration.async_execution is not None:
            should_use_async_extension = integration.async_execution
        else:
            # try to figure it out ourselves
            if StrawberrySentryAsyncExtension in extensions:
                should_use_async_extension = True
            elif StrawberrySentrySyncExtension in extensions:
                should_use_async_extension = False
            else:
                should_use_async_extension = bool(
                    {"starlette", "starlite", "litestar", "fastapi"}
                    & set(_get_installed_modules())
                )

            logger.info(
                "Assuming strawberry is running in %s context. If not, initialize it as StrawberryIntegration(async_execution=%s).",
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

        return old_schema_init(self, *args, **kwargs)

    Schema.__init__ = _sentry_patched_schema_init


class SentryAsyncExtension(SchemaExtension):
    def __init__(
        self,
        *,
        execution_context=None,
    ):
        # type: (Any, Optional[ExecutionContext]) -> None
        if execution_context:
            self.execution_context = execution_context

    @cached_property
    def _resource_name(self):
        assert self.execution_context.query

        query_hash = self.hash_query(self.execution_context.query)

        if self.execution_context.operation_name:
            return f"{self.execution_context.operation_name}:{query_hash}"

        return query_hash

    def hash_query(self, query):
        # type: (str) -> str
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    def on_operation(self):
        # type: () -> Generator[None, None, None]
        self._operation_name = self.execution_context.operation_name
        name = f"{self._operation_name}" if self._operation_name else "Anonymous Query"

        with configure_scope() as scope:
            if scope.span:
                self.gql_span = scope.span.start_child(
                    op="gql",
                    description=name,
                )
            else:
                self.gql_span = start_span(
                    op="gql",
                )

        operation_type = "query"

        assert self.execution_context.query

        if self.execution_context.query.strip().startswith("mutation"):
            operation_type = "mutation"
        if self.execution_context.query.strip().startswith("subscription"):
            operation_type = "subscription"

        self.gql_span.set_tag("graphql.operation_type", operation_type)
        self.gql_span.set_tag("graphql.resource_name", self._resource_name)
        self.gql_span.set_data("graphql.query", self.execution_context.query)

        yield

        self.gql_span.finish()

    def on_validate(self):
        # type: () -> Generator[None, None, None]
        self.validation_span = self.gql_span.start_child(
            op="validation", description="Validation"
        )

        yield

        self.validation_span.finish()

    def on_parse(self):
        # type: () -> Generator[None, None, None]
        self.parsing_span = self.gql_span.start_child(
            op="parsing", description="Parsing"
        )

        yield

        self.parsing_span.finish()

    def should_skip_tracing(self, _next, info):
        # type: (Callable, GraphQLResolveInfo) -> bool
        return should_skip_tracing(_next, info)

    async def resolve(self, _next, root, info, *args, **kwargs):
        # type: (Callable, Any, GraphQLResolveInfo, str, Any) -> Any
        if self.should_skip_tracing(_next, info):
            result = _next(root, info, *args, **kwargs)

            if isawaitable(result):  # pragma: no cover
                result = await result

            return result

        field_path = f"{info.parent_type}.{info.field_name}"

        with self.gql_span.start_child(
            op="resolve", description=f"Resolving: {field_path}"
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
    def resolve(self, _next, root, info, *args, **kwargs):
        # type: (Callable, Any, GraphQLResolveInfo, str, Any) -> Any
        if self.should_skip_tracing(_next, info):
            return _next(root, info, *args, **kwargs)

        field_path = f"{info.parent_type}.{info.field_name}"

        with self.gql_span.start_child(
            op="resolve", description=f"Resolving: {field_path}"
        ) as span:
            span.set_tag("graphql.field_name", info.field_name)
            span.set_tag("graphql.parent_type", info.parent_type.name)
            span.set_tag("graphql.field_path", field_path)
            span.set_tag("graphql.path", ".".join(map(str, info.path.as_list())))

            return _next(root, info, *args, **kwargs)


def _patch_execute():
    # type: () -> None
    old_execute_async = strawberry_schema.execute
    old_execute_sync = strawberry_schema.execute_sync

    async def _sentry_patched_execute_async(*args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(StrawberryIntegration)
        if integration is None:
            return await old_execute_async(*args, **kwargs)

        result = await old_execute_async(*args, **kwargs)

        if "execution_context" in kwargs and result.errors:
            with hub.configure_scope() as scope:
                event_processor = _make_request_event_processor(
                    kwargs["execution_context"]
                )
                scope.add_event_processor(event_processor)

        return result

    def _sentry_patched_execute_sync(*args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(StrawberryIntegration)
        if integration is None:
            return old_execute_sync(*args, **kwargs)

        result = old_execute_sync(*args, **kwargs)

        if "execution_context" in kwargs and result.errors:
            with hub.configure_scope() as scope:
                event_processor = _make_request_event_processor(
                    kwargs["execution_context"]
                )
                scope.add_event_processor(event_processor)

        return result

    strawberry_schema.execute = _sentry_patched_execute_async
    strawberry_schema.execute_sync = _sentry_patched_execute_sync


def _patch_process_result():
    # type: () -> None
    old_process_result = strawberry_http.process_result

    def _sentry_patched_process_result(result, *args, **kwargs):
        hub = Hub.current
        integration = hub.get_integration(StrawberryIntegration)
        if integration is None:
            return old_process_result(result, *args, **kwargs)

        processed_result = old_process_result(result, *args, **kwargs)

        if result.errors:
            with hub.configure_scope() as scope:
                event_processor = _make_response_event_processor(processed_result)
                scope.add_event_processor(event_processor)

        with capture_internal_exceptions():
            for error in result.errors or []:
                event, hint = event_from_exception(
                    error,
                    client_options=hub.client.options if hub.client else None,
                    mechanism={
                        "type": integration.identifier,
                        "handled": False,
                    },
                )
                hub.capture_event(event, hint=hint)

        return processed_result

    strawberry_http.process_result = _sentry_patched_process_result
    async_base_view.process_result = _sentry_patched_process_result
    sync_base_view.process_result = _sentry_patched_process_result
    fastapi_router.process_result = _sentry_patched_process_result


def _make_request_event_processor(execution_context):
    # type: (ExecutionContext) -> EventProcessor

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        with capture_internal_exceptions():
            if _should_send_default_pii():
                request_data = event.setdefault("request", {})
                request_data["api_target"] = "graphql"

                if not request_data.get("data"):
                    request_data["data"] = {"query": execution_context.query}

                    if execution_context.variables:
                        request_data["data"]["variables"] = execution_context.variables
                    if execution_context.operation_name:
                        request_data["data"][
                            "operationName"
                        ] = execution_context.operation_name

            elif event.get("request", {}).get("data"):
                del event["request"]["data"]

        return event

    return inner


def _make_response_event_processor(data):
    # type: (Dict[str, Any]) -> EventProcessor

    def inner(event, hint):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
        with capture_internal_exceptions():
            if _should_send_default_pii():
                contexts = event.setdefault("contexts", {})
                contexts["response"] = {"data": data}

        return event

    return inner
