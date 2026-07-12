import functools
import json
import sys
import warnings
from collections.abc import Mapping as MappingABC
from collections.abc import MutableMapping as MutableMappingABC
from collections.abc import Set
from copy import deepcopy
from json import JSONDecodeError
from types import FunctionType
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk._types import OVER_SIZE_LIMIT_SUBSTITUTE
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations import (
    _DEFAULT_FAILED_REQUEST_STATUS_CODES,
    DidNotEnable,
    Integration,
)
from sentry_sdk.integrations._asgi_common import _RootPathInPath
from sentry_sdk.integrations._wsgi_common import (
    DEFAULT_HTTP_METHODS_TO_CAPTURE,
    HttpCodeRangeContainer,
    _is_json_content_type,
    request_body_within_bounds,
)
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan, get_current_span
from sentry_sdk.tracing import SOURCE_FOR_STYLE, TransactionSource
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    event_from_exception,
    parse_version,
    transaction_from_function,
)

if TYPE_CHECKING:
    from typing import (
        Any,
        Awaitable,
        Callable,
        Container,
        Dict,
        Mapping,
        MutableMapping,
        Optional,
        Tuple,
        Union,
    )

    from sentry_sdk._types import Event, Hint, HttpStatusCodeRange

try:
    from lilya import __version__ as LILYA_VERSION
except ImportError:
    raise DidNotEnable("Lilya is not installed")

version = parse_version(LILYA_VERSION)

if version is None:
    raise DidNotEnable("Unparsable Lilya version: {}".format(LILYA_VERSION))

if version < (0, 27, 0):
    raise DidNotEnable("Lilya 0.27.0 or newer is required")

try:
    from lilya._internal import _exception_handlers as exception_handlers_module
    from lilya._internal import _responses as responses_module
    from lilya._internal._responses import BaseHandler
    from lilya.apps import Lilya
    from lilya.concurrency import run_in_threadpool
    from lilya.datastructures import DataUpload
    from lilya.middleware import exceptions as middleware_exceptions_module
    from lilya.middleware.authentication import AuthenticationMiddleware
    from lilya.middleware.base import DefineMiddleware
    from lilya.middleware.exceptions import ExceptionMiddleware
    from lilya.requests import Request
    from lilya.types import Empty
except (ImportError, SyntaxError):
    raise DidNotEnable("Lilya is not installed")


if sys.version_info >= (3, 14):
    from inspect import iscoroutinefunction
else:
    from asyncio import iscoroutinefunction


_DEFAULT_TRANSACTION_NAME = "generic Lilya request"

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class LilyaIntegration(Integration):
    """Adds Sentry instrumentation for Lilya applications."""

    identifier = "lilya"
    origin = "auto.http.lilya"

    transaction_style = ""

    def __init__(
        self,
        transaction_style: str = "url",
        failed_request_status_codes: "Union[Set[int], list[HttpStatusCodeRange], None]" = _DEFAULT_FAILED_REQUEST_STATUS_CODES,
        middleware_spans: bool = False,
        http_methods_to_capture: "tuple[str, ...]" = DEFAULT_HTTP_METHODS_TO_CAPTURE,
    ) -> None:
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )

        self.transaction_style = transaction_style
        self.middleware_spans = middleware_spans
        self.http_methods_to_capture = tuple(map(str.upper, http_methods_to_capture))

        if isinstance(failed_request_status_codes, Set):
            self.failed_request_status_codes: "Container[int]" = (
                failed_request_status_codes
            )
        else:
            warnings.warn(
                "Passing a list or None for failed_request_status_codes is deprecated. "
                "Please pass a set of int instead.",
                DeprecationWarning,
                stacklevel=2,
            )

            if failed_request_status_codes is None:
                self.failed_request_status_codes = _DEFAULT_FAILED_REQUEST_STATUS_CODES
            else:
                self.failed_request_status_codes = HttpCodeRangeContainer(
                    failed_request_status_codes
                )

    @staticmethod
    def setup_once() -> None:
        """Install Lilya patches for request, middleware, auth, and error paths."""
        patch_asgi_app()
        patch_middlewares()
        patch_authentication_middleware()
        patch_exception_middleware()
        patch_handle_exception()
        patch_handle_response()


def patch_asgi_app() -> None:
    """Wrap Lilya applications so every app instance is traced by default."""
    old_app = Lilya.__call__

    async def _sentry_patched_asgi_app(
        self: "Any",
        scope: "MutableMapping[str, Any]",
        receive: "Callable[[], Awaitable[MutableMapping[str, Any]]]",
        send: "Callable[[MutableMapping[str, Any]], Awaitable[None]]",
    ) -> "Any":
        integration = sentry_sdk.get_client().get_integration(LilyaIntegration)
        if integration is None:
            return await old_app(self, scope, receive, send)

        middleware = SentryAsgiMiddleware(
            lambda *a, **kw: old_app(self, *a, **kw),
            mechanism_type=LilyaIntegration.identifier,
            transaction_style=integration.transaction_style,
            span_origin=LilyaIntegration.origin,
            http_methods_to_capture=(
                integration.http_methods_to_capture
                if integration
                else DEFAULT_HTTP_METHODS_TO_CAPTURE
            ),
            asgi_version=3,
            root_path_in_path=_RootPathInPath.EITHER,
        )

        return await middleware(scope, receive, send)

    Lilya.__call__ = _sentry_patched_asgi_app  # type: ignore[method-assign]


def _is_async_callable(obj: "Any") -> bool:
    """Return whether an object is callable through an async call path."""
    while isinstance(obj, functools.partial):
        obj = obj.func

    return iscoroutinefunction(obj) or (
        callable(obj) and iscoroutinefunction(obj.__call__)  # type: ignore[operator]
    )


def _get_route_path_template(scope: "MutableMapping[str, Any]") -> "Optional[str]":
    """Return Lilya's routed path template, including mounted application prefixes."""
    template = scope.get("route_path_template")
    if template is None:
        route = scope.get("route")
        if route is not None:
            template = getattr(route, "path", None)

    if template is None:
        return None

    app_root_path = scope.get("app_root_path") or ""
    root_path = scope.get("root_path") or ""
    mount_prefix = ""

    if root_path:
        if app_root_path and root_path.startswith(app_root_path):
            mount_prefix = root_path[len(app_root_path) :]
        elif not app_root_path:
            mount_prefix = root_path

    if mount_prefix:
        template = "%s/%s" % (mount_prefix.rstrip("/"), template.lstrip("/"))

    if not template.startswith("/"):
        template = "/%s" % template

    return template


def _get_transaction_name_and_source(
    transaction_style: str,
    scope: "MutableMapping[str, Any]",
) -> "Tuple[str, TransactionSource]":
    """Resolve the Sentry transaction name from Lilya's ASGI scope."""
    if transaction_style == "endpoint":
        endpoint = scope.get("handler")
        if endpoint is not None:
            name = transaction_from_function(endpoint)
            if name:
                return name, TransactionSource.COMPONENT

    template = _get_route_path_template(scope)
    if template is not None:
        return template, TransactionSource.ROUTE

    return _DEFAULT_TRANSACTION_NAME, SOURCE_FOR_STYLE[transaction_style]


def _has_lilya_transaction_data(
    transaction_style: str,
    scope: "MutableMapping[str, Any]",
) -> bool:
    """Return whether Lilya has populated route data on the scope."""
    if transaction_style == "endpoint" and scope.get("handler") is not None:
        return True

    return _get_route_path_template(scope) is not None


def _set_transaction_name_and_source(
    scope: "MutableMapping[str, Any]",
    transaction_style: str,
) -> None:
    """Set transaction naming on the current and isolation scopes."""
    name, source = _get_transaction_name_and_source(transaction_style, scope)
    sentry_sdk.set_transaction_name(name, source)
    sentry_sdk.get_isolation_scope().set_transaction_name(name, source)


def _get_cached_request_body_attribute(
    client: "Any",
    request: "Request",
) -> "Optional[str]":
    """Return cached request body data suitable for span attributes."""
    if "content-length" not in request.headers:
        return None

    try:
        content_length = int(request.headers["content-length"])
    except ValueError:
        return None

    if content_length and not request_body_within_bounds(client, content_length):
        return OVER_SIZE_LIMIT_SUBSTITUTE

    json_body = getattr(request, "_json", Empty)
    if json_body is not Empty:
        return json.dumps(json_body)

    formdata_body: "Any" = getattr(request, "_form", Empty)
    if formdata_body is Empty or formdata_body is None:
        return None

    form_data: "Dict[str, Any]" = {}
    for key, val in formdata_body.items():
        is_file = isinstance(val, DataUpload)
        form_data[key] = val if not is_file else "[Unparsable]"

    return json.dumps(form_data)


def patch_handle_response() -> None:
    """Wrap Lilya endpoint adapters to attach request data and handled errors.

    Lilya builds an ASGI app from each handler through `BaseHandler`. Patching
    that adapter lets the integration name route transactions, preserve the
    endpoint call path, and capture configured handled HTTP exceptions without
    changing the user's handler signature.
    """
    old_handle_response = BaseHandler.handle_response

    def _sentry_handle_response(
        self: "Any",
        func: "Callable[..., Any]",
        other_signature: "Any" = None,
    ) -> "Callable[..., Awaitable[Any]]":
        if _is_async_callable(func):

            @functools.wraps(func)
            async def _sentry_lilya_endpoint(*args: "Any", **kwargs: "Any") -> "Any":
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    _capture_handled_status_exception(exc)
                    raise

        else:

            @functools.wraps(func)
            def _sentry_lilya_endpoint(*args: "Any", **kwargs: "Any") -> "Any":
                _update_active_thread()
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    _capture_handled_status_exception(exc)
                    raise

        handler_app = old_handle_response(self, _sentry_lilya_endpoint, other_signature)

        async def _sentry_lilya_handler_app(
            scope: "MutableMapping[str, Any]",
            receive: "Callable[[], Awaitable[MutableMapping[str, Any]]]",
            send: "Callable[[MutableMapping[str, Any]], Awaitable[None]]",
        ) -> "Any":
            client = sentry_sdk.get_client()
            integration = client.get_integration(LilyaIntegration)

            if integration is None or scope.get("type") != "http":
                return await handler_app(scope, receive, send)

            request = Request(scope, receive, send)
            _set_transaction_name_and_source(scope, integration.transaction_style)

            sentry_scope = sentry_sdk.get_isolation_scope()
            extractor = LilyaRequestExtractor(request)
            info = await extractor.extract_request_info()
            exception_handlers = scope.get("lilya.exception_handlers")
            _wrap_exception_handler_maps(exception_handlers)

            def event_processor(event: "Event", _hint: "Hint") -> "Event":
                request_info = event.get("request", {})
                if info:
                    if "cookies" in info:
                        request_info["cookies"] = info["cookies"]
                    if "data" in info:
                        request_info["data"] = info["data"]
                event["request"] = deepcopy(request_info)

                return event

            sentry_scope._name = LilyaIntegration.identifier
            sentry_scope.add_event_processor(event_processor)
            current_span = get_current_span()

            try:
                return await handler_app(scope, receive, send)
            finally:
                if type(current_span) is StreamedSpan:
                    body = _get_cached_request_body_attribute(client, request)
                    if body is not None:
                        current_span._segment.set_attribute(
                            SPANDATA.HTTP_REQUEST_BODY_DATA, body
                        )

        return _sentry_lilya_handler_app

    BaseHandler.handle_response = _sentry_handle_response  # type: ignore[method-assign]


def patch_middlewares() -> None:
    """Patch middleware definitions so configured user middleware emits spans."""
    old_middleware_init = DefineMiddleware.__init__

    not_yet_patched = "_sentry_middleware_init" not in str(old_middleware_init)
    if not_yet_patched:

        def _sentry_middleware_init(
            self: "Any", cls: "Any", *args: "Any", **kwargs: "Any"
        ) -> None:
            old_middleware_init(self, cls, *args, **kwargs)

            with capture_internal_exceptions():
                middleware_class = self.middleware
                if middleware_class is SentryAsgiMiddleware:
                    return
                _enable_span_for_middleware(middleware_class)

        DefineMiddleware.__init__ = _sentry_middleware_init  # type: ignore[method-assign]


def _enable_span_for_middleware(middleware_class: "Any") -> None:
    """Wrap a Lilya middleware call with middleware, receive, and send spans."""
    old_call = middleware_class.__call__

    async def _create_span_call(
        self: "Any",
        scope: "MutableMapping[str, Any]",
        receive: "Callable[..., Awaitable[MutableMapping[str, Any]]]",
        send: "Callable[[MutableMapping[str, Any]], Awaitable[None]]",
    ) -> "Any":
        client = sentry_sdk.get_client()
        integration = client.get_integration(LilyaIntegration)
        if integration is None or not integration.middleware_spans:
            return await old_call(self, scope, receive, send)

        if _has_lilya_transaction_data(integration.transaction_style, scope):
            _set_transaction_name_and_source(scope, integration.transaction_style)

        middleware_name = self.__class__.__name__
        if has_span_streaming_enabled(client.options):
            with sentry_sdk.traces.start_span(
                name=middleware_name,
                attributes={
                    "sentry.op": OP.MIDDLEWARE_LILYA,
                    "sentry.origin": LilyaIntegration.origin,
                },
            ) as middleware_span:
                middleware_span.set_attribute(SPANDATA.MIDDLEWARE_NAME, middleware_name)

                async def _sentry_receive(
                    *args: "Any", **kwargs: "Any"
                ) -> "MutableMapping[str, Any]":
                    if client.get_integration(LilyaIntegration) is None:
                        return await receive(*args, **kwargs)
                    with sentry_sdk.traces.start_span(
                        name=getattr(receive, "__qualname__", str(receive)),
                        attributes={
                            "sentry.op": OP.MIDDLEWARE_LILYA_RECEIVE,
                            "sentry.origin": LilyaIntegration.origin,
                        },
                    ) as span:
                        span.set_attribute(SPANDATA.MIDDLEWARE_NAME, middleware_name)
                        return await receive(*args, **kwargs)

                receive_name = getattr(receive, "__name__", str(receive))
                receive_patched = receive_name == "_sentry_receive"
                new_receive = _sentry_receive if not receive_patched else receive

                async def _sentry_send(message: "MutableMapping[str, Any]") -> None:
                    if client.get_integration(LilyaIntegration) is None:
                        return await send(message)
                    with sentry_sdk.traces.start_span(
                        name=getattr(send, "__qualname__", str(send)),
                        attributes={
                            "sentry.op": OP.MIDDLEWARE_LILYA_SEND,
                            "sentry.origin": LilyaIntegration.origin,
                        },
                    ) as span:
                        span.set_attribute(SPANDATA.MIDDLEWARE_NAME, middleware_name)
                        return await send(message)

                send_name = getattr(send, "__name__", str(send))
                send_patched = send_name == "_sentry_send"
                new_send = _sentry_send if not send_patched else send

                return await old_call(self, scope, new_receive, new_send)

        else:
            with sentry_sdk.start_span(
                op=OP.MIDDLEWARE_LILYA,
                name=middleware_name,
                origin=LilyaIntegration.origin,
            ) as middleware_span:
                middleware_span.set_tag("lilya.middleware_name", middleware_name)

                async def _sentry_receive(
                    *args: "Any", **kwargs: "Any"
                ) -> "MutableMapping[str, Any]":
                    if client.get_integration(LilyaIntegration) is None:
                        return await receive(*args, **kwargs)
                    with sentry_sdk.start_span(
                        op=OP.MIDDLEWARE_LILYA_RECEIVE,
                        name=getattr(receive, "__qualname__", str(receive)),
                        origin=LilyaIntegration.origin,
                    ) as span:
                        span.set_tag("lilya.middleware_name", middleware_name)
                        return await receive(*args, **kwargs)

                receive_name = getattr(receive, "__name__", str(receive))
                receive_patched = receive_name == "_sentry_receive"
                new_receive = _sentry_receive if not receive_patched else receive

                async def _sentry_send(message: "MutableMapping[str, Any]") -> None:
                    if client.get_integration(LilyaIntegration) is None:
                        return await send(message)
                    with sentry_sdk.start_span(
                        op=OP.MIDDLEWARE_LILYA_SEND,
                        name=getattr(send, "__qualname__", str(send)),
                        origin=LilyaIntegration.origin,
                    ) as span:
                        span.set_tag("lilya.middleware_name", middleware_name)
                        return await send(message)

                send_name = getattr(send, "__name__", str(send))
                send_patched = send_name == "_sentry_send"
                new_send = _sentry_send if not send_patched else send

                return await old_call(self, scope, new_receive, new_send)

    not_yet_patched = old_call.__name__ not in ["_create_span_call"]

    if not_yet_patched:
        middleware_class.__call__ = _create_span_call


def patch_authentication_middleware() -> None:
    """Patch authentication so downstream events can include Lilya's user."""
    old_authenticate = AuthenticationMiddleware.authenticate

    async def _sentry_patched_authenticate(
        self: "Any",
        conn: "Any",
        **kwargs: "Any",
    ) -> "Any":
        auth_result = await old_authenticate(self, conn, **kwargs)
        if isinstance(auth_result, tuple) and len(auth_result) > 1:
            _set_user_to_sentry_scope(auth_result[1])

        return auth_result

    not_yet_patched = old_authenticate.__name__ != "_sentry_patched_authenticate"
    if not_yet_patched:
        AuthenticationMiddleware.authenticate = _sentry_patched_authenticate  # type: ignore[method-assign]


def patch_exception_middleware() -> None:
    """Patch exception middleware so handled errors keep Sentry context."""
    old_init = ExceptionMiddleware.__init__
    old_call = ExceptionMiddleware.__call__

    def _sentry_patched_exception_init(
        self: "Any", *args: "Any", **kwargs: "Any"
    ) -> None:
        old_init(self, *args, **kwargs)
        _wrap_exception_handlers(self._exception_handlers)
        _wrap_exception_handlers(self._status_handlers)

    async def _sentry_patched_exception_call(
        self: "Any",
        scope: "MutableMapping[str, Any]",
        receive: "Callable[[], Awaitable[MutableMapping[str, Any]]]",
        send: "Callable[[MutableMapping[str, Any]], Awaitable[None]]",
    ) -> "Any":
        _add_user_to_sentry_scope(scope)
        return await old_call(self, scope, receive, send)

    not_yet_patched = old_init.__name__ != "_sentry_patched_exception_init"
    if not_yet_patched:
        ExceptionMiddleware.__init__ = _sentry_patched_exception_init  # type: ignore[method-assign]

    not_yet_call_patched = old_call.__name__ != "_sentry_patched_exception_call"
    if not_yet_call_patched:
        ExceptionMiddleware.__call__ = _sentry_patched_exception_call  # type: ignore[method-assign]


def patch_handle_exception() -> None:
    """Patch Lilya's shared handled-exception dispatcher as a fallback path."""
    patched: "set[int]" = set()
    for module in (
        exception_handlers_module,
        responses_module,
        middleware_exceptions_module,
    ):
        handle_exception = getattr(module, "handle_exception", None)
        if (
            not isinstance(handle_exception, FunctionType)
            or id(handle_exception) in patched
        ):
            continue
        patched.add(id(handle_exception))
        _patch_handle_exception_function(handle_exception)


def _copy_function(func: FunctionType) -> FunctionType:
    """Copy a Lilya function before patching the original in-place."""
    copied = FunctionType(
        func.__code__,
        func.__globals__,
        name=func.__name__,
        argdefs=func.__defaults__,
        closure=func.__closure__,
    )
    copied.__kwdefaults__ = func.__kwdefaults__
    copied.__dict__.update(func.__dict__)
    copied.__annotations__ = dict(func.__annotations__)
    return copied


def _patch_handle_exception_function(handle_exception: FunctionType) -> None:
    """Patch a Lilya handled-exception dispatcher without replacing it."""
    if handle_exception.__name__ == "_sentry_patched_handle_exception":
        return

    old_handle_exception = _copy_function(handle_exception)

    async def _sentry_patched_handle_exception(
        scope: "MutableMapping[str, Any]",
        conn: "Any",
        exc: Exception,
        handler: "Callable[..., Any]",
        _old_handle_exception: FunctionType = old_handle_exception,
        _capture: "Callable[[Exception], None]" = _capture_handled_status_exception,
    ) -> "Any":
        _capture(exc)

        return await _old_handle_exception(scope, conn, exc, handler)

    handle_exception.__code__ = _sentry_patched_handle_exception.__code__
    handle_exception.__defaults__ = _sentry_patched_handle_exception.__defaults__
    handle_exception.__kwdefaults__ = _sentry_patched_handle_exception.__kwdefaults__
    handle_exception.__name__ = "_sentry_patched_handle_exception"
    handle_exception.__qualname__ = "_sentry_patched_handle_exception"
    handle_exception.__annotations__ = dict(
        _sentry_patched_handle_exception.__annotations__
    )


def _wrap_exception_handler_maps(handler_maps: "Any") -> None:
    """Wrap the exception handler maps Lilya stores on the ASGI scope."""
    if not handler_maps:
        return

    if isinstance(handler_maps, MutableMappingABC):
        _wrap_exception_handlers(handler_maps)
        return

    if not isinstance(handler_maps, (list, tuple)):
        return

    for handler_map in handler_maps:
        if isinstance(handler_map, MutableMappingABC):
            _wrap_exception_handlers(handler_map)


def _wrap_exception_handlers(
    handler_map: "MutableMapping[Any, Callable[..., Any]]",
) -> None:
    """Wrap Lilya exception and status handlers in-place."""
    for key, handler in list(handler_map.items()):
        if getattr(handler, "__name__", None) == "_sentry_lilya_exception_handler":
            continue
        handler_map[key] = _wrap_exception_handler(handler)


def _wrap_exception_handler(
    handler: "Callable[..., Any]",
) -> "Callable[[Any, Exception], Awaitable[Any]]":
    """Create a Sentry-aware Lilya exception handler."""

    async def _sentry_lilya_exception_handler(request: "Any", exc: Exception) -> "Any":
        _capture_handled_status_exception(exc)

        if _is_async_callable(handler):
            return await handler(request, exc)

        return await run_in_threadpool(handler, request, exc)

    return _sentry_lilya_exception_handler


def _update_active_thread() -> None:
    """Set active transaction and profile thread data for sync Lilya handlers."""
    client = sentry_sdk.get_client()
    if client.get_integration(LilyaIntegration) is None:
        return

    current_scope = sentry_sdk.get_current_scope()
    if has_span_streaming_enabled(client.options):
        current_span = current_scope.streamed_span
        if type(current_span) is StreamedSpan:
            current_span._segment._update_active_thread()
    elif current_scope.transaction is not None:
        current_scope.transaction.update_active_thread()

    sentry_scope = sentry_sdk.get_isolation_scope()
    if sentry_scope.profile is not None:
        sentry_scope.profile.update_active_thread_id()


def _capture_exception(exc: BaseException, handled: bool = False) -> None:
    """Capture an exception using Lilya's Sentry mechanism metadata."""
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": LilyaIntegration.identifier, "handled": handled},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _capture_handled_status_exception(exc: Exception) -> None:
    """Capture handled Lilya HTTP exceptions configured as failed requests."""
    if getattr(exc, "_sentry_lilya_captured", False):
        return

    integration = sentry_sdk.get_client().get_integration(LilyaIntegration)
    if (
        integration is not None
        and getattr(exc, "status_code", None) in integration.failed_request_status_codes
    ):
        setattr(exc, "_sentry_lilya_captured", True)
        _capture_exception(exc, handled=True)


def _add_user_to_sentry_scope(scope: "MutableMapping[str, Any]") -> None:
    """Copy Lilya's authenticated user from the ASGI scope into Sentry."""
    _set_user_to_sentry_scope(scope.get("user"))


def _set_user_to_sentry_scope(scope_user: "Any") -> None:
    """Copy a Lilya user object into Sentry's standard user fields."""
    if not should_send_default_pii():
        return

    user = _retrieve_user_from_scope_user(scope_user)
    if user is None:
        return

    sentry_sdk.get_isolation_scope().set_user(user)


def _retrieve_user_from_scope_user(scope_user: "Any") -> "Optional[Dict[str, Any]]":
    """Extract Sentry user fields from a Lilya scope user."""
    if not scope_user:
        return None

    if isinstance(scope_user, MappingABC):
        return _select_sentry_user_fields(scope_user)

    user_info: "Dict[str, Any]" = {}

    if hasattr(scope_user, "asdict"):
        asdict_user = scope_user.asdict()
        if isinstance(asdict_user, MappingABC):
            user_info.update(_select_sentry_user_fields(asdict_user))

    user_id = getattr(scope_user, "unique_identifier", None)
    if user_id:
        user_info["id"] = str(user_id)

    username = getattr(scope_user, "display_name", None) or getattr(
        scope_user, "username", None
    )
    if username:
        user_info["username"] = str(username)

    email = getattr(scope_user, "email", None)
    if email:
        user_info["email"] = str(email)

    return user_info or None


def _select_sentry_user_fields(user_info: "Mapping[str, Any]") -> "Dict[str, Any]":
    """Return only fields supported by Sentry's user payload."""
    return {
        key: str(user_info[key])
        for key in ("id", "username", "email")
        if user_info.get(key) is not None and user_info.get(key) != ""
    }


class LilyaRequestExtractor:
    """Extracts Lilya request data for Sentry events."""

    def __init__(self, request: "Request") -> None:
        self.request = request

    async def extract_request_info(self) -> "Dict[str, Any]":
        """Return request body and cookie data collected without consuming handlers."""
        client = sentry_sdk.get_client()

        request_info: "Dict[str, Any]" = {}

        with capture_internal_exceptions():
            if should_send_default_pii():
                request_info["cookies"] = self.cookies()

            content_length = await self.content_length()
            if not content_length:
                return request_info

            if content_length and not request_body_within_bounds(
                client, content_length
            ):
                request_info["data"] = AnnotatedValue.removed_because_over_size_limit()
                return request_info

            if self.is_form():
                return request_info

            json_body = await self.json()
            if json_body is not Empty:
                request_info["data"] = json_body
                return request_info

            request_info["data"] = AnnotatedValue.removed_because_raw_data()
            return request_info

    async def content_length(self) -> "Optional[int]":
        if "content-length" in self.request.headers:
            try:
                return int(self.request.headers["content-length"])
            except ValueError:
                return None

        return None

    def cookies(self) -> "Dict[str, Any]":
        return self.request.cookies

    def is_form(self) -> bool:
        content_type = self.request.headers.get("content-type")
        media_type = (content_type or "").split(";", 1)[0].lower()
        return media_type in (
            "multipart/form-data",
            "application/x-www-form-urlencoded",
        )

    def is_json(self) -> bool:
        return _is_json_content_type(self.request.headers.get("content-type"))

    async def json(self) -> "Any":
        if not self.is_json():
            return Empty
        try:
            return await self.request.json()
        except JSONDecodeError:
            return Empty
