import json
from contextlib import contextmanager
from decimal import Decimal
from functools import wraps
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from google.protobuf.descriptor import FieldDescriptor
from httpx import Response, Request

import grpc

import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.qdrant.consts import (
    _DISALLOWED_REST_FIELDS,
    _DISALLOWED_PROTO_FIELDS,
    _qdrant_trie,
    _IDENTIFIER,
)

# Hack to get new Python features working in older versions
# without introducing a hard dependency on `typing_extensions`
# from: https://stackoverflow.com/a/71944042/300572
# taken from sentry_sdk.integrations.grpc.__init__.py
if TYPE_CHECKING:
    from typing import (
        Any,
        Callable,
        Awaitable,
        ParamSpec,
        Optional,
        Sequence,
        Generator,
    )
    from grpc import Channel, ClientCallDetails
    from grpc.aio import UnaryUnaryCall
    from grpc.aio import Channel as AsyncChannel
    from google.protobuf.message import Message
    from sentry_sdk.tracing import Span, Transaction, _SpanRecorder
    from sentry_sdk.integrations.qdrant import QdrantIntegration
else:
    # Fake ParamSpec
    class ParamSpec:
        def __init__(self, _):
            self.args = None
            self.kwargs = None

    # Callable[anything] will return None
    class _Callable:
        def __getitem__(self, _):
            return None

    # Make instances
    Callable = _Callable()

P = ParamSpec("P")


def _remove_httpx_span(span, operation_id):
    # type: (Span, str) -> None
    current_transaction = span.containing_transaction  # type: Optional[Transaction]
    if not current_transaction:
        return

    span_recorder = current_transaction._span_recorder  # type: Optional[_SpanRecorder]
    if not span_recorder:
        return

    try:
        current_span_index = span_recorder.spans.index(span)
    except ValueError:
        # what ?
        return

    next_span_index = current_span_index + 1
    if next_span_index >= len(span_recorder.spans):
        return

    next_span = span_recorder.spans[next_span_index]  # type: Span

    # check if the next span is an HTTPX client span
    if next_span.op != OP.HTTP_CLIENT or next_span.origin != HttpxIntegration.origin:
        return

    httpx_span_description = next_span.description  # type: Optional[str]
    if not httpx_span_description:
        return

    try:
        httpx_method, httpx_url = httpx_span_description.split(" ", 1)
    except ValueError:
        # unexpected span name format
        return

    parsed_url = urlparse(httpx_url)
    httpx_path = parsed_url.path

    # just to be *really* sure that we don't accidentally delete an unrelated span
    httpx_operation_id = _qdrant_trie.match(httpx_path, httpx_method)

    if httpx_operation_id == operation_id:
        span_recorder.spans.pop(next_span_index)


@contextmanager
def _prepare_span(request, mute_http_child_span):
    # type: (Request, bool) -> Span
    operation_id = _qdrant_trie.match(request.url.path, request.method)
    payload = json.loads(request.content) if request.content else {}
    payload["operation_id"] = operation_id

    name = json.dumps(_strip_dict(payload, _DISALLOWED_REST_FIELDS), default=str)

    span = sentry_sdk.start_span(
        op=OP.DB_QDRANT_REST,
        name=name,
        origin=_IDENTIFIER,
    )
    span.set_data("db.api", "REST")
    span.set_data(SPANDATA.DB_SYSTEM, "qdrant")
    span.set_data(SPANDATA.DB_OPERATION, operation_id)

    collection_name = getattr(request, "collection_name", None)
    if collection_name:
        span.set_data("db.qdrant.collection", collection_name)

    yield span

    # the HttpxIntegration will capture all REST calls, leading to almost duplicate spans but with less information.
    # we mute the created span by httpx by default, but you can disable this in the Qdrant integration if you want.
    if mute_http_child_span:
        _remove_httpx_span(span, operation_id)

    span.finish()


def _get_integration():
    # type: () -> (Optional[QdrantIntegration])
    from sentry_sdk.integrations.qdrant import QdrantIntegration

    return sentry_sdk.get_client().get_integration(QdrantIntegration)


def _sync_api_client_send_inner(f):
    # type: (Callable[P, Response]) -> Callable[P, Response]
    @wraps(f)
    def wrapper(*args, **kwargs):
        # type: (P.args, P.kwargs) -> Response
        integration = _get_integration()  # type: Optional[QdrantIntegration]
        if integration is None or not (len(args) >= 2 and isinstance(args[1], Request)):
            return f(*args, **kwargs)

        request = args[1]  # type: Request
        with _prepare_span(request, integration.mute_children_http_spans):
            return f(*args, **kwargs)

    return wrapper


def _async_api_client_send_inner(f):
    # type: (Callable[P, Awaitable[Response]]) -> Callable[P, Awaitable[Response]]
    @wraps(f)
    async def wrapper(*args, **kwargs):
        # type: (P.args, P.kwargs) -> Response
        integration = _get_integration()  # type: Optional[QdrantIntegration]
        if integration is None or not (len(args) >= 2 and isinstance(args[1], Request)):
            return f(*args, **kwargs)

        request = args[1]  # type: Request
        with _prepare_span(request, integration.mute_children_http_spans):
            return await f(*args, **kwargs)

    return wrapper


# taken from grpc integration
def _wrap_channel_sync(f):
    # type: (Callable[P, Channel]) -> Callable[P, Channel]

    @wraps(f)
    def patched_channel(*args, **kwargs):
        # type: (P.args, P.kwargs) -> Channel
        channel = f(*args, **kwargs)

        if not ClientInterceptor._is_intercepted:
            ClientInterceptor._is_intercepted = True
            return grpc.intercept_channel(channel, ClientInterceptor())
        else:
            return channel

    return patched_channel


def _wrap_channel_async(func):
    # type: (Callable[P, AsyncChannel]) -> Callable[P, AsyncChannel]
    "Wrapper for asynchronous secure and insecure channel."

    @wraps(func)
    def patched_channel(*args, interceptors, **kwargs):
        # type: (P.args, Optional[Sequence[grpc.aio.ClientInterceptor]], P.kwargs) -> Channel
        sentry_interceptors = [
            AsyncClientInterceptor(),
        ]
        interceptors = [*sentry_interceptors, *(interceptors or [])]
        return func(*args, interceptors=interceptors, **kwargs)

    return patched_channel


class GenericClientInterceptor:
    _is_intercepted = False

    @staticmethod
    def _update_client_call_details_metadata_from_scope(client_call_details):
        # type: (ClientCallDetails) -> ClientCallDetails
        """
        Updates the metadata of the client call details by appending
        trace propagation headers from the current Sentry scope.
        """
        metadata = (
            list(client_call_details.metadata) if client_call_details.metadata else []
        )

        # append sentrys trace propagation headers
        for (
            key,
            value,
        ) in sentry_sdk.get_current_scope().iter_trace_propagation_headers():
            metadata.append((key, value))

        updated_call_details = grpc._interceptor._ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
            compression=client_call_details.compression,
        )

        return updated_call_details

    @staticmethod
    def _should_intercept(method):
        # type: (str) -> bool
        """
        Determines whether the interceptor should process the given method.
        """
        if not method.startswith("/qdrant."):
            return False

        from sentry_sdk.integrations.qdrant import QdrantIntegration

        return sentry_sdk.get_client().get_integration(QdrantIntegration) is not None

    @staticmethod
    def _prepare_tags(method, request):
        # type: (str, Message) -> dict
        """
        Prepares the tags for the Sentry span based on the method and request.
        """
        # qdrant uses this prefix for all its methods
        operation = method[len("/qdrant.") :]

        tags = {
            SPANDATA.DB_SYSTEM: "qdrant",
            SPANDATA.DB_OPERATION: operation,
        }

        collection_name = getattr(request, "collection_name", None)
        if collection_name:
            tags["db.qdrant.collection"] = collection_name

        return tags

    @contextmanager
    def _start_span(self, request, tags):
        # type: (Message, dict) -> Generator[Span, None, None]
        """
        Starts a new Sentry span for the gRPC call and sets the provided tags.
        """
        span = sentry_sdk.start_span(
            op=OP.DB_QDRANT_GRPC,
            name=json.dumps(
                _strip_dict(_protobuf_to_dict(request), _DISALLOWED_PROTO_FIELDS),
                default=str,
            ),
            origin=_IDENTIFIER,
        )
        try:
            for tag, value in tags.items():
                span.set_data(tag, value)
                span.set_tag(tag, value)
            yield span
        finally:
            span.finish()


class ClientInterceptor(GenericClientInterceptor, grpc.UnaryUnaryClientInterceptor):
    def intercept_unary_unary(self, continuation, client_call_details, request):
        # type: (Callable[[ClientCallDetails, Message], UnaryUnaryCall], ClientCallDetails, Message) -> UnaryUnaryCall
        """
        Intercepts synchronous unary-unary gRPC calls to add Sentry tracing.
        """
        method = client_call_details.method

        if not self._should_intercept(method):
            return continuation(client_call_details, request)

        tags = self._prepare_tags(method, request)
        with self._start_span(request, tags) as span:
            span.set_data("db.api", "gRPC")
            updated_call_details = self._update_client_call_details_metadata_from_scope(
                client_call_details
            )

            response = continuation(updated_call_details, request)
            span.set_data("code", response.code().name)

            return response


class AsyncClientInterceptor(
    GenericClientInterceptor, grpc.aio.UnaryUnaryClientInterceptor
):
    async def intercept_unary_unary(self, continuation, client_call_details, request):
        # type: (Callable[[ClientCallDetails, Message], UnaryUnaryCall], ClientCallDetails, Message) -> UnaryUnaryCall
        """
        Intercepts asynchronous unary-unary gRPC calls to add Sentry tracing.
        """
        method = client_call_details.method

        if not self._should_intercept(method):
            return await continuation(client_call_details, request)

        tags = self._prepare_tags(method, request)

        with self._start_span(request, tags) as span:
            span.set_data("db.api", "gRPC")
            updated_call_details = self._update_client_call_details_metadata_from_scope(
                client_call_details
            )

            response = await continuation(updated_call_details, request)

            status_code = await response.code()
            span.set_data("code", status_code.name)

            return response


def _protobuf_to_dict(message, prefix=""):
    # type: (Message, str) -> dict
    """
    Recursively converts a protobuf message to a dictionary, excluding unset fields.

    Args:
        message (Message): The protobuf message instance.
        prefix (str): The prefix for field names to indicate nesting.

    Returns:
        dict: A dictionary representation of the protobuf message with only set fields.
    """
    result = {}

    for field in message.DESCRIPTOR.fields:
        field_name = field.name

        full_field_name = f"{prefix}.{field_name}" if prefix else field_name

        # determine if the field is set
        if field.type == FieldDescriptor.TYPE_MESSAGE:
            if field.label == FieldDescriptor.LABEL_REPEATED:
                is_set = len(getattr(message, field_name)) > 0
            elif (
                field.message_type.has_options
                and field.message_type.GetOptions().map_entry
            ):
                is_set = len(getattr(message, field_name)) > 0
            else:
                is_set = message.HasField(field_name)
        else:
            if field.label == FieldDescriptor.LABEL_REPEATED:
                is_set = len(getattr(message, field_name)) > 0
            else:
                # for scalar fields, check presence if possible
                try:
                    is_set = message.HasField(field_name)
                except ValueError:
                    # HasField not available (e.g., proto3 without optional)
                    # fallback: consider non-default values as set
                    default_value = field.default_value
                    value = getattr(message, field_name)
                    is_set = value != default_value

        if not is_set:
            # field is either not set or has a default value
            continue

        value = getattr(message, field_name)

        if field.type == FieldDescriptor.TYPE_MESSAGE:
            if field.label == FieldDescriptor.LABEL_REPEATED:
                # repeated message fields: list of dicts
                result[field_name] = [
                    _protobuf_to_dict(item, prefix=full_field_name) for item in value
                ]
            elif (
                field.message_type.has_options
                and field.message_type.GetOptions().map_entry
            ):
                # map dict fields
                result[field_name] = {key: val for key, val in value.items()}
            else:
                # single nested message
                result[field_name] = _protobuf_to_dict(value, prefix=full_field_name)
        elif field.label == FieldDescriptor.LABEL_REPEATED:
            # repeated scalar fields
            result[field_name] = list(value)
        else:
            # scalar field
            result[field_name] = value

    return result


def _get_forbidden_field_placeholder(value, depth=0, max_depth=5):
    # type: (Any, int, int) -> str
    """
    Generates a placeholder string based on the type of the input value.

    Args:
        value (Any): The value to generate a placeholder for.
        depth (int): Current recursion depth.
        max_depth (int): Maximum recursion depth to prevent excessive recursion.

    Returns:
        str: A placeholder string representing the type of the input value.
    """
    if depth > max_depth:
        return "..."

    if isinstance(value, bool) or isinstance(value, str):
        return "%s"
    elif isinstance(value, int):
        return "%d"
    elif isinstance(value, (float, Decimal)):
        return "%f"
    elif isinstance(value, bytes):
        return "b'...'"
    elif value is None:
        return "null"
    elif isinstance(value, list):
        if not value:
            return "[]"
        # handle heterogeneous lists by representing each element
        placeholders = [
            _get_forbidden_field_placeholder(item, depth + 1, max_depth)
            for item in value
        ]
        max_items = 3
        if len(placeholders) > max_items:
            placeholders = placeholders[:max_items] + ["..."]
        return f"[{', '.join(placeholders)}]"
    elif isinstance(value, tuple):
        if not value:
            return "()"
        placeholders = [
            _get_forbidden_field_placeholder(item, depth + 1, max_depth)
            for item in value
        ]
        max_items = 3
        if len(placeholders) > max_items:
            placeholders = placeholders[:max_items] + ["..."]
        return f"({', '.join(placeholders)})"
    elif isinstance(value, set):
        if not value:
            return "set()"
        placeholders = [
            _get_forbidden_field_placeholder(item, depth + 1, max_depth)
            for item in sorted(value, key=lambda x: str(x))
        ]
        max_items = 3
        if len(placeholders) > max_items:
            placeholders = placeholders[:max_items] + ["..."]
        return f"{{{', '.join(placeholders)}}}"
    elif isinstance(value, dict):
        if not value:
            return "{}"
        # represent keys and values
        placeholders = []
        max_items = 3
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                placeholders.append("...")
                break
            key_placeholder = _get_forbidden_field_placeholder(k, depth + 1, max_depth)
            value_placeholder = _get_forbidden_field_placeholder(
                v, depth + 1, max_depth
            )
            placeholders.append(f"{key_placeholder}: {value_placeholder}")
        return f"{{{', '.join(placeholders)}}}"
    elif isinstance(value, complex):
        return "(%f+%fj)" % (value.real, value.imag)
    else:
        # just use the class name at this point
        return f"<{type(value).__name__}>"


def _strip_dict(data, disallowed_fields):
    # type: (dict, set) -> dict
    for k, v in data.items():
        if isinstance(v, dict):
            data[k] = _strip_dict(v, disallowed_fields)
        elif k in disallowed_fields:
            data[k] = _get_forbidden_field_placeholder(v)
        elif isinstance(v, list):
            data[k] = [
                _strip_dict(item, disallowed_fields) if isinstance(item, dict) else item
                for item in v
            ]
    return data
