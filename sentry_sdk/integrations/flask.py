from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration, _check_minimum_version
from sentry_sdk.integrations._wsgi_common import (
    _RAW_DATA_EXCEPTIONS,
    DEFAULT_HTTP_METHODS_TO_CAPTURE,
    RequestExtractor,
    _serialize_request_body_data,
    request_body_within_bounds,
)
from sentry_sdk.integrations.wsgi import SentryWsgiMiddleware
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.traces import StreamedSpan, _get_current_streamed_span
from sentry_sdk.tracing import SOURCE_FOR_STYLE
from sentry_sdk.tracing_utils import has_span_streaming_enabled
from sentry_sdk.utils import (
    AnnotatedValue,
    capture_internal_exceptions,
    ensure_integration_enabled,
    event_from_exception,
    package_version,
)

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, Union

    from werkzeug.datastructures import FileStorage, ImmutableMultiDict

    from sentry_sdk._types import Event, EventProcessor
    from sentry_sdk.integrations.wsgi import _ScopedResponse


try:
    import flask_login  # type: ignore
except ImportError:
    flask_login = None

try:
    from flask import Flask, Request  # type: ignore
    from flask import request as flask_request
    from flask.signals import (
        before_render_template,
        got_request_exception,
        request_started,
    )
    from markupsafe import Markup
except ImportError:
    raise DidNotEnable("Flask is not installed")

try:
    import blinker  # noqa
except ImportError:
    raise DidNotEnable("blinker is not installed")

TRANSACTION_STYLE_VALUES = ("endpoint", "url")


class FlaskIntegration(Integration):
    identifier = "flask"
    origin = f"auto.http.{identifier}"

    transaction_style = ""

    def __init__(
        self,
        transaction_style: str = "endpoint",
        http_methods_to_capture: "tuple[str, ...]" = DEFAULT_HTTP_METHODS_TO_CAPTURE,
    ) -> None:
        if transaction_style not in TRANSACTION_STYLE_VALUES:
            raise ValueError(
                "Invalid value for transaction_style: %s (must be in %s)"
                % (transaction_style, TRANSACTION_STYLE_VALUES)
            )
        self.transaction_style = transaction_style
        self.http_methods_to_capture = tuple(map(str.upper, http_methods_to_capture))

    @staticmethod
    def setup_once() -> None:
        try:
            from quart import Quart  # type: ignore

            if Flask == Quart:
                # This is Quart masquerading as Flask, don't enable the Flask
                # integration. See https://github.com/getsentry/sentry-python/issues/2709
                raise DidNotEnable(
                    "This is not a Flask app but rather Quart pretending to be Flask"
                )
        except ImportError:
            pass

        version = package_version("flask")
        _check_minimum_version(FlaskIntegration, version)

        before_render_template.connect(_add_sentry_trace)
        request_started.connect(_request_started)
        got_request_exception.connect(_capture_exception)

        old_app = Flask.__call__

        def sentry_patched_wsgi_app(
            self: "Any", environ: "Dict[str, str]", start_response: "Callable[..., Any]"
        ) -> "_ScopedResponse":
            integration = sentry_sdk.get_client().get_integration(FlaskIntegration)
            if integration is None:
                return old_app(self, environ, start_response)

            middleware = SentryWsgiMiddleware(
                lambda *a, **kw: old_app(self, *a, **kw),
                span_origin=FlaskIntegration.origin,
                http_methods_to_capture=(
                    integration.http_methods_to_capture
                    if integration
                    else DEFAULT_HTTP_METHODS_TO_CAPTURE
                ),
            )
            return middleware(environ, start_response)

        Flask.__call__ = sentry_patched_wsgi_app


def _add_sentry_trace(
    sender: "Flask", template: "Any", context: "Dict[str, Any]", **extra: "Any"
) -> None:
    if "sentry_trace" in context:
        return

    scope = sentry_sdk.get_current_scope()
    trace_meta = Markup(scope.trace_propagation_meta())
    context["sentry_trace"] = trace_meta  # for backwards compatibility
    context["sentry_trace_meta"] = trace_meta


def _set_transaction_name_and_source(
    scope: "sentry_sdk.Scope", transaction_style: str, request: "Request"
) -> None:
    try:
        name_for_style = {
            "url": request.url_rule.rule,
            "endpoint": request.url_rule.endpoint,
        }
        scope.set_transaction_name(
            name_for_style[transaction_style],
            source=SOURCE_FOR_STYLE[transaction_style],
        )
    except Exception:
        pass


def _request_started(app: "Flask", **kwargs: "Any") -> None:
    integration = sentry_sdk.get_client().get_integration(FlaskIntegration)
    if integration is None:
        return

    request = flask_request._get_current_object()

    # Set the transaction name and source here,
    # but rely on WSGI middleware to actually start the transaction
    _set_transaction_name_and_source(
        sentry_sdk.get_current_scope(), integration.transaction_style, request
    )

    scope = sentry_sdk.get_isolation_scope()
    evt_processor = _make_request_event_processor(app, request, integration)
    scope.add_event_processor(evt_processor)

    client = sentry_sdk.get_client()
    if has_span_streaming_enabled(client.options):
        _set_request_body_data_on_streaming_segment(request, client)


def _set_request_body_data_on_streaming_segment(
    request: "Request", client: "sentry_sdk.client.BaseClient"
) -> None:
    current_span = _get_current_streamed_span()
    if type(current_span) is not StreamedSpan:
        return

    with capture_internal_exceptions():
        content_length = int(request.content_length or 0)
        extractor = FlaskRequestExtractor(request)

        if not request_body_within_bounds(client, content_length):
            data = AnnotatedValue.substituted_because_over_size_limit()
        else:
            raw_data = None
            try:
                raw_data = extractor.raw_data()
            except _RAW_DATA_EXCEPTIONS:
                pass

            parsed_body = extractor.parsed_body()
            if parsed_body is not None:
                data = parsed_body
            elif raw_data:
                data = AnnotatedValue.substituted_because_raw_data()
            else:
                return

        current_span._segment.set_attribute(
            "http.request.body.data",
            _serialize_request_body_data(data),
        )


class FlaskRequestExtractor(RequestExtractor):
    def env(self) -> "Dict[str, str]":
        return self.request.environ

    def cookies(self) -> "Dict[Any, Any]":
        return {
            k: v[0] if isinstance(v, list) and len(v) == 1 else v
            for k, v in self.request.cookies.items()
        }

    def raw_data(self) -> bytes:
        return self.request.get_data()

    def form(self) -> "ImmutableMultiDict[str, Any]":
        return self.request.form

    def files(self) -> "ImmutableMultiDict[str, Any]":
        return self.request.files

    def is_json(self) -> bool:
        return self.request.is_json

    def json(self) -> "Any":
        return self.request.get_json(silent=True)

    def size_of_file(self, file: "FileStorage") -> int:
        return file.content_length


def _make_request_event_processor(
    app: "Flask", request: "Callable[[], Request]", integration: "FlaskIntegration"
) -> "EventProcessor":
    def inner(event: "Event", hint: "dict[str, Any]") -> "Event":
        # if the request is gone we are fine not logging the data from
        # it.  This might happen if the processor is pushed away to
        # another thread.
        if request is None:
            return event

        with capture_internal_exceptions():
            FlaskRequestExtractor(request).extract_into_event(event)

        if should_send_default_pii():
            with capture_internal_exceptions():
                _add_user_to_event(event)

        return event

    return inner


@ensure_integration_enabled(FlaskIntegration)
def _capture_exception(
    sender: "Flask", exception: "Union[ValueError, BaseException]", **kwargs: "Any"
) -> None:
    event, hint = event_from_exception(
        exception,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "flask", "handled": False},
    )

    sentry_sdk.capture_event(event, hint=hint)


def _add_user_to_event(event: "Event") -> None:
    if flask_login is None:
        return

    user = flask_login.current_user
    if user is None:
        return

    with capture_internal_exceptions():
        # Access this object as late as possible as accessing the user
        # is relatively costly

        user_info = event.setdefault("user", {})

        try:
            user_info.setdefault("id", user.get_id())
            # TODO: more configurable user attrs here
        except AttributeError:
            # might happen if:
            # - flask_login could not be imported
            # - flask_login is not configured
            # - no user is logged in
            pass

        # The following attribute accesses are ineffective for the general
        # Flask-Login case, because the User interface of Flask-Login does not
        # care about anything but the ID. However, Flask-User (based on
        # Flask-Login) documents a few optional extra attributes.
        #
        # https://github.com/lingthio/Flask-User/blob/a379fa0a281789618c484b459cb41236779b95b1/docs/source/data_models.rst#fixed-data-model-property-names

        try:
            user_info.setdefault("email", user.email)
        except Exception:
            pass

        try:
            user_info.setdefault("username", user.username)
        except Exception:
            pass
