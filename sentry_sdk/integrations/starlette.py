import asyncio
import functools
import warnings
from collections.abc import Set
from copy import deepcopy
from json import JSONDecodeError
import json
from urllib.parse import parse_qsl, unquote_plus
from dataclasses import dataclass, field
from enum import Enum
from tempfile import SpooledTemporaryFile

import sentry_sdk
from sentry_sdk.consts import OP
from sentry_sdk.integrations import (
    DidNotEnable,
    Integration,
    _DEFAULT_FAILED_REQUEST_STATUS_CODES,
)
from sentry_sdk.integrations._wsgi_common import (
    DEFAULT_HTTP_METHODS_TO_CAPTURE,
    HttpCodeRangeContainer,
    _is_json_content_type,
    request_body_within_bounds,
)
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing import (
    SOURCE_FOR_STYLE,
    TransactionSource,
)
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    parse_version,
    transaction_from_function,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Awaitable, Callable, Container, Dict, Optional, Tuple, Union

    from sentry_sdk._types import Event, HttpStatusCodeRange
try:
    import starlette  # type: ignore
    from starlette import __version__ as STARLETTE_VERSION
    from starlette.applications import Starlette  # type: ignore
    from starlette.datastructures import UploadFile, FormData, Headers  # type: ignore
    from starlette.middleware import Middleware  # type: ignore
    from starlette.middleware.authentication import (  # type: ignore
        AuthenticationMiddleware,
    )
    from starlette.requests import Request  # type: ignore
    from starlette.routing import Match  # type: ignore
    from starlette.types import ASGIApp, Receive, Scope as StarletteScope, Send  # type: ignore
except ImportError:
    raise DidNotEnable("Starlette is not installed")

try:
    # Starlette 0.20
    from starlette.middleware.exceptions import ExceptionMiddleware  # type: ignore
except ImportError:
    # Startlette 0.19.1
    from starlette.exceptions import ExceptionMiddleware  # type: ignore

try:
    # Optional dependency of Starlette to parse form data.
    try:
        # python-multipart 0.0.13 and later
        import python_multipart as multipart  # type: ignore
        from python_multipart.multipart import (
            parse_options_header,
            MultipartCallbacks,
            QuerystringCallbacks,
        )
    except ImportError:
        # python-multipart 0.0.12 and earlier
        import multipart  # type: ignore
        from multipart.multipart import (
            parse_options_header,
            MultipartCallbacks,
            QuerystringCallbacks,
        )
except ImportError:
    multipart = None
    parse_options_header = None

_DEFAULT_TRANSACTION_NAME = "generic Starlette request"

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class StarletteIntegration(Integration):
    identifier = "starlette"
    origin = f"auto.http.{identifier}"

    transaction_style = ""

    def __init__(
        self,
        transaction_style="url",  # type: str
        failed_request_status_codes=_DEFAULT_FAILED_REQUEST_STATUS_CODES,  # type: Union[Set[int], list[HttpStatusCodeRange], None]
        middleware_spans=True,  # type: bool
        http_methods_to_capture=DEFAULT_HTTP_METHODS_TO_CAPTURE,  # type: tuple[str, ...]
    ):
        # type: (...) -> None
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style
        self.middleware_spans = middleware_spans
        self.http_methods_to_capture = tuple(map(str.upper, http_methods_to_capture))

        if isinstance(failed_request_status_codes, Set):
            self.failed_request_status_codes = (
                failed_request_status_codes
            )  # type: Container[int]
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
    def setup_once():
        # type: () -> None
        version = parse_version(STARLETTE_VERSION)

        if version is None:
            raise DidNotEnable(
                "Unparsable Starlette version: {}".format(STARLETTE_VERSION)
            )

        patch_middlewares()
        patch_asgi_app()
        patch_request_response()

        if version >= (0, 24):
            patch_templates()


def _enable_span_for_middleware(middleware_class):
    # type: (Any) -> type
    old_call = middleware_class.__call__

    async def _create_span_call(app, scope, receive, send, **kwargs):
        # type: (Any, Dict[str, Any], Callable[[], Awaitable[Dict[str, Any]]], Callable[[Dict[str, Any]], Awaitable[None]], Any) -> None
        integration = sentry_sdk.get_client().get_integration(StarletteIntegration)
        if integration is None or not integration.middleware_spans:
            return await old_call(app, scope, receive, send, **kwargs)

        middleware_name = app.__class__.__name__

        # Update transaction name with middleware name
        name, source = _get_transaction_from_middleware(app, scope, integration)
        if name is not None:
            sentry_sdk.get_current_scope().set_transaction_name(
                name,
                source=source,
            )

        with sentry_sdk.start_span(
            op=OP.MIDDLEWARE_STARLETTE,
            name=middleware_name,
            origin=StarletteIntegration.origin,
        ) as middleware_span:
            middleware_span.set_tag("starlette.middleware_name", middleware_name)

            # Creating spans for the "receive" callback
            async def _sentry_receive(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                with sentry_sdk.start_span(
                    op=OP.MIDDLEWARE_STARLETTE_RECEIVE,
                    name=getattr(receive, "__qualname__", str(receive)),
                    origin=StarletteIntegration.origin,
                ) as span:
                    span.set_tag("starlette.middleware_name", middleware_name)
                    return await receive(*args, **kwargs)

            receive_name = getattr(receive, "__name__", str(receive))
            receive_patched = receive_name == "_sentry_receive"
            new_receive = _sentry_receive if not receive_patched else receive

            # Creating spans for the "send" callback
            async def _sentry_send(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                with sentry_sdk.start_span(
                    op=OP.MIDDLEWARE_STARLETTE_SEND,
                    name=getattr(send, "__qualname__", str(send)),
                    origin=StarletteIntegration.origin,
                ) as span:
                    span.set_tag("starlette.middleware_name", middleware_name)
                    return await send(*args, **kwargs)

            send_name = getattr(send, "__name__", str(send))
            send_patched = send_name == "_sentry_send"
            new_send = _sentry_send if not send_patched else send

            return await old_call(app, scope, new_receive, new_send, **kwargs)

    not_yet_patched = old_call.__name__ not in [
        "_create_span_call",
        "_sentry_authenticationmiddleware_call",
        "_sentry_exceptionmiddleware_call",
    ]

    if not_yet_patched:
        middleware_class.__call__ = _create_span_call

    return middleware_class


@ensure_integration_enabled(StarletteIntegration)
def _capture_exception(exception, handled=False):
    # type: (BaseException, **Any) -> None
    event, hint = event_from_exception(
        exception,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": StarletteIntegration.identifier, "handled": handled},
    )

    sentry_sdk.capture_event(event, hint=hint)


def patch_exception_middleware(middleware_class):
    # type: (Any) -> None
    """
    Capture all exceptions in Starlette app and
    also extract user information.
    """
    old_middleware_init = middleware_class.__init__

    not_yet_patched = "_sentry_middleware_init" not in str(old_middleware_init)

    if not_yet_patched:

        def _sentry_middleware_init(self, *args, **kwargs):
            # type: (Any, Any, Any) -> None
            old_middleware_init(self, *args, **kwargs)

            # Patch existing exception handlers
            old_handlers = self._exception_handlers.copy()

            async def _sentry_patched_exception_handler(self, *args, **kwargs):
                # type: (Any, Any, Any) -> None
                integration = sentry_sdk.get_client().get_integration(
                    StarletteIntegration
                )

                exp = args[0]

                if integration is not None:
                    is_http_server_error = (
                        hasattr(exp, "status_code")
                        and isinstance(exp.status_code, int)
                        and exp.status_code in integration.failed_request_status_codes
                    )
                    if is_http_server_error:
                        _capture_exception(exp, handled=True)

                # Find a matching handler
                old_handler = None
                for cls in type(exp).__mro__:
                    if cls in old_handlers:
                        old_handler = old_handlers[cls]
                        break

                if old_handler is None:
                    return

                if _is_async_callable(old_handler):
                    return await old_handler(self, *args, **kwargs)
                else:
                    return old_handler(self, *args, **kwargs)

            for key in self._exception_handlers.keys():
                self._exception_handlers[key] = _sentry_patched_exception_handler

        middleware_class.__init__ = _sentry_middleware_init

        old_call = middleware_class.__call__

        async def _sentry_exceptionmiddleware_call(self, scope, receive, send):
            # type: (Dict[str, Any], Dict[str, Any], Callable[[], Awaitable[Dict[str, Any]]], Callable[[Dict[str, Any]], Awaitable[None]]) -> None
            # Also add the user (that was eventually set by be Authentication middle
            # that was called before this middleware). This is done because the authentication
            # middleware sets the user in the scope and then (in the same function)
            # calls this exception middelware. In case there is no exception (or no handler
            # for the type of exception occuring) then the exception bubbles up and setting the
            # user information into the sentry scope is done in auth middleware and the
            # ASGI middleware will then send everything to Sentry and this is fine.
            # But if there is an exception happening that the exception middleware here
            # has a handler for, it will send the exception directly to Sentry, so we need
            # the user information right now.
            # This is why we do it here.
            _add_user_to_sentry_scope(scope)
            await old_call(self, scope, receive, send)

        middleware_class.__call__ = _sentry_exceptionmiddleware_call


@ensure_integration_enabled(StarletteIntegration)
def _add_user_to_sentry_scope(scope):
    # type: (Dict[str, Any]) -> None
    """
    Extracts user information from the ASGI scope and
    adds it to Sentry's scope.
    """
    if "user" not in scope:
        return

    if not should_send_default_pii():
        return

    user_info = {}  # type: Dict[str, Any]
    starlette_user = scope["user"]

    username = getattr(starlette_user, "username", None)
    if username:
        user_info.setdefault("username", starlette_user.username)

    user_id = getattr(starlette_user, "id", None)
    if user_id:
        user_info.setdefault("id", starlette_user.id)

    email = getattr(starlette_user, "email", None)
    if email:
        user_info.setdefault("email", starlette_user.email)

    sentry_scope = sentry_sdk.get_isolation_scope()
    sentry_scope.user = user_info


def patch_authentication_middleware(middleware_class):
    # type: (Any) -> None
    """
    Add user information to Sentry scope.
    """
    old_call = middleware_class.__call__

    not_yet_patched = "_sentry_authenticationmiddleware_call" not in str(old_call)

    if not_yet_patched:

        async def _sentry_authenticationmiddleware_call(self, scope, receive, send):
            # type: (Dict[str, Any], Dict[str, Any], Callable[[], Awaitable[Dict[str, Any]]], Callable[[Dict[str, Any]], Awaitable[None]]) -> None
            await old_call(self, scope, receive, send)
            _add_user_to_sentry_scope(scope)

        middleware_class.__call__ = _sentry_authenticationmiddleware_call


def patch_middlewares():
    # type: () -> None
    """
    Patches Starlettes `Middleware` class to record
    spans for every middleware invoked.
    """
    old_middleware_init = Middleware.__init__

    not_yet_patched = "_sentry_middleware_init" not in str(old_middleware_init)

    if not_yet_patched:

        def _sentry_middleware_init(self, cls, *args, **kwargs):
            # type: (Any, Any, Any, Any) -> None
            if cls == SentryAsgiMiddleware:
                return old_middleware_init(self, cls, *args, **kwargs)

            span_enabled_cls = _enable_span_for_middleware(cls)
            old_middleware_init(self, span_enabled_cls, *args, **kwargs)

            if cls == AuthenticationMiddleware:
                patch_authentication_middleware(cls)

            if cls == ExceptionMiddleware:
                patch_exception_middleware(cls)

        Middleware.__init__ = _sentry_middleware_init


def patch_asgi_app():
    # type: () -> None
    """
    Instrument Starlette ASGI app using the SentryAsgiMiddleware.
    """
    old_app = Starlette.__call__

    async def _sentry_patched_asgi_app(self, scope, receive, send):
        # type: (Starlette, StarletteScope, Receive, Send) -> None
        integration = sentry_sdk.get_client().get_integration(StarletteIntegration)
        if integration is None:
            return await old_app(self, scope, receive, send)

        middleware = SentryAsgiMiddleware(
            lambda *a, **kw: old_app(self, *a, **kw),
            mechanism_type=StarletteIntegration.identifier,
            transaction_style=integration.transaction_style,
            span_origin=StarletteIntegration.origin,
            http_methods_to_capture=(
                integration.http_methods_to_capture
                if integration
                else DEFAULT_HTTP_METHODS_TO_CAPTURE
            ),
            asgi_version=3,
        )

        return await middleware(scope, receive, send)

    Starlette.__call__ = _sentry_patched_asgi_app


# This was vendored in from Starlette to support Starlette 0.19.1 because
# this function was only introduced in 0.20.x
def _is_async_callable(obj):
    # type: (Any) -> bool
    while isinstance(obj, functools.partial):
        obj = obj.func

    return asyncio.iscoroutinefunction(obj) or (
        callable(obj) and asyncio.iscoroutinefunction(obj.__call__)
    )


def _patch_request(request):
    _original_body = request.body
    _original_json = request.json
    _original_form = request.form

    def restore_original_methods():
        request.body = _original_body
        request.json = _original_json
        request.form = _original_form

    async def sentry_body():
        request.scope["state"]["sentry_sdk.body"] = await _original_body()
        restore_original_methods()
        return request.scope["state"]["sentry_sdk.body"]

    async def sentry_json():
        request.scope["state"]["sentry_sdk.json"] = await _original_json()
        restore_original_methods()
        return request.scope["state"]["sentry_sdk.json"]

    async def sentry_form():
        request.scope["state"]["sentry_sdk.form"] = await _original_form()
        restore_original_methods()
        return request.scope["state"]["sentry_sdk.form"]

    request.body = sentry_body
    request.json = sentry_json
    request.form = sentry_form


def patch_request_response():
    # type: () -> None
    old_request_response = starlette.routing.request_response

    def _sentry_request_response(func):
        # type: (Callable[[Any], Any]) -> ASGIApp
        old_func = func

        is_coroutine = _is_async_callable(old_func)
        if is_coroutine:

            async def _sentry_async_func(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                integration = sentry_sdk.get_client().get_integration(
                    StarletteIntegration
                )
                if integration is None:
                    return await old_func(*args, **kwargs)

                request = args[0]
                _patch_request(request)

                _set_transaction_name_and_source(
                    sentry_sdk.get_current_scope(),
                    integration.transaction_style,
                    request,
                )

                sentry_scope = sentry_sdk.get_isolation_scope()

                def _make_request_event_processor(req, integration):
                    # type: (Any, Any) -> Callable[[Event, dict[str, Any]], Event]
                    def event_processor(event, hint):
                        # type: (Event, Dict[str, Any]) -> Event

                        # Add info from request to event
                        extractor = StarletteRequestExtractor(request)
                        info = extractor.extract_request_info(req.scope)

                        request_info = event.get("request", {})
                        if info:
                            if "cookies" in info:
                                request_info["cookies"] = info["cookies"]
                            if "data" in info:
                                request_info["data"] = info["data"]
                        event["request"] = deepcopy(request_info)

                        return event

                    return event_processor

                sentry_scope._name = StarletteIntegration.identifier
                sentry_scope.add_event_processor(
                    _make_request_event_processor(request, integration)
                )

                return await old_func(*args, **kwargs)

            func = _sentry_async_func

        else:

            @functools.wraps(old_func)
            def _sentry_sync_func(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                integration = sentry_sdk.get_client().get_integration(
                    StarletteIntegration
                )
                if integration is None:
                    return old_func(*args, **kwargs)

                current_scope = sentry_sdk.get_current_scope()
                if current_scope.transaction is not None:
                    current_scope.transaction.update_active_thread()

                sentry_scope = sentry_sdk.get_isolation_scope()
                if sentry_scope.profile is not None:
                    sentry_scope.profile.update_active_thread_id()

                request = args[0]

                _set_transaction_name_and_source(
                    sentry_scope, integration.transaction_style, request
                )

                extractor = StarletteRequestExtractor(request)
                cookies = extractor.extract_cookies_from_request()

                def _make_request_event_processor(req, integration):
                    # type: (Any, Any) -> Callable[[Event, dict[str, Any]], Event]
                    def event_processor(event, hint):
                        # type: (Event, dict[str, Any]) -> Event

                        # Extract information from request
                        request_info = event.get("request", {})
                        if cookies:
                            request_info["cookies"] = cookies

                        event["request"] = deepcopy(request_info)

                        return event

                    return event_processor

                sentry_scope._name = StarletteIntegration.identifier
                sentry_scope.add_event_processor(
                    _make_request_event_processor(request, integration)
                )

                return old_func(*args, **kwargs)

            func = _sentry_sync_func

        return old_request_response(func)

    starlette.routing.request_response = _sentry_request_response


def patch_templates():
    # type: () -> None

    # If markupsafe is not installed, then Jinja2 is not installed
    # (markupsafe is a dependency of Jinja2)
    # In this case we do not need to patch the Jinja2Templates class
    try:
        from markupsafe import Markup
    except ImportError:
        return  # Nothing to do

    from starlette.templating import Jinja2Templates  # type: ignore

    old_jinja2templates_init = Jinja2Templates.__init__

    not_yet_patched = "_sentry_jinja2templates_init" not in str(
        old_jinja2templates_init
    )

    if not_yet_patched:

        def _sentry_jinja2templates_init(self, *args, **kwargs):
            # type: (Jinja2Templates, *Any, **Any) -> None
            def add_sentry_trace_meta(request):
                # type: (Request) -> Dict[str, Any]
                trace_meta = Markup(
                    sentry_sdk.get_current_scope().trace_propagation_meta()
                )
                return {
                    "sentry_trace_meta": trace_meta,
                }

            kwargs.setdefault("context_processors", [])

            if add_sentry_trace_meta not in kwargs["context_processors"]:
                kwargs["context_processors"].append(add_sentry_trace_meta)

            return old_jinja2templates_init(self, *args, **kwargs)

        Jinja2Templates.__init__ = _sentry_jinja2templates_init


def _is_form_data_encoded(ct):
    # type: (Optional[str]) -> bool
    mt = (ct or "").split(";", 1)[0]
    return mt == "multipart/form-data"


def _is_form_urlencoded(ct):
    # type: (Optional[str]) -> bool
    mt = (ct or "").split(";", 1)[0]
    return mt == "application/x-www-form-urlencoded"


# Adapted from Starlette and adapted to work in a synchronous context
# https://github.com/Kludex/starlette/blob/main/starlette/formparsers.py


class FormMessage(Enum):
    FIELD_START = 1
    FIELD_NAME = 2
    FIELD_DATA = 3
    FIELD_END = 4
    END = 5


@dataclass
class MultipartPart:
    content_disposition: bytes | None = None
    field_name: str = ""
    data: bytearray = field(default_factory=bytearray)
    file: UploadFile | None = None
    item_headers: list[tuple[bytes, bytes]] = field(default_factory=list)


def _user_safe_decode(src: bytes | bytearray, codec: str) -> str:
    try:
        return src.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return src.decode("latin-1")


class MultiPartException(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class FormParser:
    def __init__(self, headers: Headers, content: bytes) -> None:
        assert (
            multipart is not None
        ), "The `python-multipart` library must be installed to use form parsing."
        self.headers = headers
        self.content = content
        self.messages: list[tuple[FormMessage, bytes]] = []

    def on_field_start(self) -> None:
        message = (FormMessage.FIELD_START, b"")
        self.messages.append(message)

    def on_field_name(self, data: bytes, start: int, end: int) -> None:
        message = (FormMessage.FIELD_NAME, data[start:end])
        self.messages.append(message)

    def on_field_data(self, data: bytes, start: int, end: int) -> None:
        message = (FormMessage.FIELD_DATA, data[start:end])
        self.messages.append(message)

    def on_field_end(self) -> None:
        message = (FormMessage.FIELD_END, b"")
        self.messages.append(message)

    def on_end(self) -> None:
        message = (FormMessage.END, b"")
        self.messages.append(message)

    def parse(self) -> FormData:
        # Callbacks dictionary.
        callbacks: QuerystringCallbacks = {
            "on_field_start": self.on_field_start,
            "on_field_name": self.on_field_name,
            "on_field_data": self.on_field_data,
            "on_field_end": self.on_field_end,
            "on_end": self.on_end,
        }

        # Create the parser.
        parser = multipart.QuerystringParser(callbacks)
        field_name = b""
        field_value = b""

        items: list[tuple[str, str | UploadFile]] = []

        parser.write(self.content)

        messages = list(self.messages)
        self.messages.clear()
        for message_type, message_bytes in messages:
            if message_type == FormMessage.FIELD_START:
                field_name = b""
                field_value = b""
            elif message_type == FormMessage.FIELD_NAME:
                field_name += message_bytes
            elif message_type == FormMessage.FIELD_DATA:
                field_value += message_bytes
            elif message_type == FormMessage.FIELD_END:
                name = unquote_plus(field_name.decode("latin-1"))
                value = unquote_plus(field_value.decode("latin-1"))
                items.append((name, value))

        return FormData(items)


class MultiPartParser:
    spool_max_size = 1024 * 1024  # 1MB
    """The maximum size of the spooled temporary file used to store file data."""
    max_part_size = 1024 * 1024  # 1MB
    """The maximum size of a part in the multipart request."""

    def __init__(
        self,
        headers: Headers,
        content: bytes,
        *,
        max_files: int | float = 1000,
        max_fields: int | float = 1000,
        max_part_size: int = 1024 * 1024,  # 1MB
    ) -> None:
        assert (
            multipart is not None
        ), "The `python-multipart` library must be installed to use form parsing."
        self.headers = headers
        self.content = content
        self.max_files = max_files
        self.max_fields = max_fields
        self.items: list[tuple[str, str | UploadFile]] = []
        self._current_files = 0
        self._current_fields = 0
        self._current_partial_header_name: bytes = b""
        self._current_partial_header_value: bytes = b""
        self._current_part = MultipartPart()
        self._charset = ""
        self._file_parts_to_write: list[tuple[MultipartPart, bytes]] = []
        self._file_parts_to_finish: list[MultipartPart] = []
        self._files_to_close_on_error: list[SpooledTemporaryFile[bytes]] = []
        self.max_part_size = max_part_size

    def on_part_begin(self) -> None:
        self._current_part = MultipartPart()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        message_bytes = data[start:end]
        if self._current_part.file is None:
            if len(self._current_part.data) + len(message_bytes) > self.max_part_size:
                raise MultiPartException(
                    f"Part exceeded maximum size of {int(self.max_part_size / 1024)}KB."
                )
            self._current_part.data.extend(message_bytes)
        else:
            self._file_parts_to_write.append((self._current_part, message_bytes))

    def on_part_end(self) -> None:
        if self._current_part.file is None:
            self.items.append(
                (
                    self._current_part.field_name,
                    _user_safe_decode(self._current_part.data, self._charset),
                )
            )
        else:
            self._file_parts_to_finish.append(self._current_part)
            # The file can be added to the items right now even though it's not
            # finished yet, because it will be finished in the `parse()` method, before
            # self.items is used in the return value.
            self.items.append((self._current_part.field_name, self._current_part.file))

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._current_partial_header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._current_partial_header_value += data[start:end]

    def on_header_end(self) -> None:
        field = self._current_partial_header_name.lower()
        if field == b"content-disposition":
            self._current_part.content_disposition = self._current_partial_header_value
        self._current_part.item_headers.append(
            (field, self._current_partial_header_value)
        )
        self._current_partial_header_name = b""
        self._current_partial_header_value = b""

    def on_headers_finished(self) -> None:
        disposition, options = parse_options_header(
            self._current_part.content_disposition
        )
        try:
            self._current_part.field_name = _user_safe_decode(
                options[b"name"], self._charset
            )
        except KeyError:
            raise MultiPartException(
                'The Content-Disposition header field "name" must be provided.'
            )
        if b"filename" in options:
            self._current_files += 1
            if self._current_files > self.max_files:
                raise MultiPartException(
                    f"Too many files. Maximum number of files is {self.max_files}."
                )
            filename = _user_safe_decode(options[b"filename"], self._charset)
            tempfile = SpooledTemporaryFile(max_size=self.spool_max_size)
            self._files_to_close_on_error.append(tempfile)
            self._current_part.file = UploadFile(
                file=tempfile,  # type: ignore[arg-type]
                size=0,
                filename=filename,
                headers=Headers(raw=self._current_part.item_headers),
            )
        else:
            self._current_fields += 1
            if self._current_fields > self.max_fields:
                raise MultiPartException(
                    f"Too many fields. Maximum number of fields is {self.max_fields}."
                )
            self._current_part.file = None

    def on_end(self) -> None:
        pass

    def parse(self) -> FormData:
        # Parse the Content-Type header to get the multipart boundary.
        _, params = parse_options_header(self.headers["Content-Type"])
        charset = params.get(b"charset", "utf-8")
        if isinstance(charset, bytes):
            charset = charset.decode("latin-1")
        self._charset = charset
        try:
            boundary = params[b"boundary"]
        except KeyError:
            raise MultiPartException("Missing boundary in multipart.")

        # Callbacks dictionary.
        callbacks: MultipartCallbacks = {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

        # Create the parser.
        parser = multipart.MultipartParser(boundary, callbacks)
        try:
            # Feed the parser with data from the request.
            parser.write(self.content)
            # Write file data, it needs to use await with the UploadFile methods
            # that call the corresponding file methods *in a threadpool*,
            # otherwise, if they were called directly in the callback methods above
            # (regular, non-async functions), that would block the event loop in
            # the main thread.
            for part, data in self._file_parts_to_write:
                assert part.file  # for type checkers
                part.file.file.write(data)
            for part in self._file_parts_to_finish:
                assert part.file  # for type checkers
                part.file.file.seek(0)
            self._file_parts_to_write.clear()
            self._file_parts_to_finish.clear()
        except MultiPartException as exc:
            # Close all the files if there was an error.
            for file in self._files_to_close_on_error:
                file.close()
            raise exc

        parser.finalize()
        return FormData(self.items)


class StarletteRequestExtractor:
    """
    Extracts useful information from the Starlette request
    (like form data or cookies) and adds it to the Sentry event.
    """

    request = None  # type: Request

    def __init__(self, request):
        # type: (StarletteRequestExtractor, Request) -> None
        self.request = request

    def extract_cookies_from_request(self):
        # type: (StarletteRequestExtractor) -> Optional[Dict[str, Any]]
        cookies = None  # type: Optional[Dict[str, Any]]
        if should_send_default_pii():
            cookies = self.cookies()

        return cookies

    def extract_request_info(self, scope):
        # type: (StarletteRequestExtractor, StarletteScope) -> Optional[Dict[str, Any]]
        client = sentry_sdk.get_client()

        request_info = {}  # type: Dict[str, Any]

        with capture_internal_exceptions():
            # Add cookies
            if should_send_default_pii():
                request_info["cookies"] = self.cookies()

            # If there is no body, just return the cookies
            content_length = self.content_length()
            if not content_length:
                return request_info

            # Add annotation if body is too big
            if content_length and not request_body_within_bounds(
                client, content_length
            ):
                request_info["data"] = AnnotatedValue.removed_because_over_size_limit()
                return request_info

            if "state" not in scope:
                return request_info

            state = scope["state"]

            if "sentry_sdk.json" in state:
                request_info["data"] = deepcopy(state["sentry_sdk.json"])
                return request_info

            if "sentry_sdk.form" in state:
                form_data = {}
                for key, val in state["sentry_sdk.form"].items():
                    is_file = isinstance(val, UploadFile)
                    form_data[key] = (
                        val
                        if not is_file
                        else AnnotatedValue.removed_because_raw_data()
                    )

                request_info["data"] = form_data
                return request_info

            if "sentry_sdk.raw_body" in state and _is_json_content_type(
                self.request.headers.get("content-type")
            ):
                try:
                    request_info["data"] = json.loads(
                        state["sentry_sdk.raw_body"].decode("utf-8")
                    )
                except JSONDecodeError:
                    return request_info
                return request_info

            if "sentry_sdk.raw_body" in state and _is_form_data_encoded(
                self.request.headers.get("content-type")
            ):

                # try:
                multipart_parser = MultiPartParser(
                    self.request.headers,
                    state["sentry_sdk.raw_body"],
                )
                request_info["data"] = multipart_parser.parse()
                # except MultiPartException as exc:
                #     if "app" in self.scope:
                #         raise HTTPException(status_code=400, detail=exc.message)
                #     raise exc

                return request_info

            elif "sentry_sdk.raw_body" in state and _is_form_data_encoded(
                self.request.headers.get("content-type")
            ):
                request_info["data"] = AnnotatedValue.removed_because_raw_data()
                return request_info

            if "sentry_sdk.raw_body" in state and _is_form_urlencoded(
                self.request.headers.get("content-type")
            ):
                request_info["data"] = parse_qsl(state["sentry_sdk.raw_body"])
                return request_info

            return request_info

    def content_length(self):
        # type: (StarletteRequestExtractor) -> Optional[int]
        if "content-length" in self.request.headers:
            return int(self.request.headers["content-length"])

        return None

    def cookies(self):
        # type: (StarletteRequestExtractor) -> Dict[str, Any]
        return self.request.cookies

    def is_json(self):
        # type: (StarletteRequestExtractor) -> bool
        return _is_json_content_type(self.request.headers.get("content-type"))


def _transaction_name_from_router(scope):
    # type: (StarletteScope) -> Optional[str]
    router = scope.get("router")
    if not router:
        return None

    for route in router.routes:
        match = route.matches(scope)
        if match[0] == Match.FULL:
            try:
                return route.path
            except AttributeError:
                # routes added via app.host() won't have a path attribute
                return scope.get("path")

    return None


def _set_transaction_name_and_source(scope, transaction_style, request):
    # type: (sentry_sdk.Scope, str, Any) -> None
    name = None
    source = SOURCE_FOR_STYLE[transaction_style]

    if transaction_style == "endpoint":
        endpoint = request.scope.get("endpoint")
        if endpoint:
            name = transaction_from_function(endpoint) or None

    elif transaction_style == "url":
        name = _transaction_name_from_router(request.scope)

    if name is None:
        name = _DEFAULT_TRANSACTION_NAME
        source = TransactionSource.ROUTE

    scope.set_transaction_name(name, source=source)


def _get_transaction_from_middleware(app, asgi_scope, integration):
    # type: (Any, Dict[str, Any], StarletteIntegration) -> Tuple[Optional[str], Optional[str]]
    name = None
    source = None

    if integration.transaction_style == "endpoint":
        name = transaction_from_function(app.__class__)
        source = TransactionSource.COMPONENT
    elif integration.transaction_style == "url":
        name = _transaction_name_from_router(asgi_scope)
        source = TransactionSource.ROUTE

    return name, source
