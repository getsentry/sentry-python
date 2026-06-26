"""
Data Collection configuration.

Implements the ``data_collection`` client option described in the Sentry SDK
"Data Collection" spec
(https://develop.sentry.dev/sdk/foundations/client/data-collection/).

``data_collection`` supersedes the single ``send_default_pii`` boolean with a
structured configuration that lets users enable or restrict automatically
collected data by category (user identity, cookies, HTTP headers, query params,
HTTP bodies, generative AI inputs/outputs, stack frame variables, source
context).

Resolution precedence (see :func:`resolve_data_collection`):

* ``data_collection`` set, ``send_default_pii`` unset -> honor ``data_collection``
  using the spec defaults for any omitted field.
* ``send_default_pii`` set, ``data_collection`` unset -> derive a
  ``DataCollection`` that mirrors what ``send_default_pii`` collects today.
* neither set -> treated as ``send_default_pii=False``.
* both set -> ``data_collection`` wins (it is the single source of truth); a
  ``DeprecationWarning`` is emitted for ``send_default_pii``.

The new collection-time filtering mechanisms (the partial-match sensitive
denylist and allow/deny key-value modes) only become active when
``data_collection`` is provided explicitly. Otherwise the SDK keeps its existing
behavior so that upgrading without configuring ``data_collection`` changes
nothing.
"""

import warnings
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode

from sentry_sdk._types import SENSITIVE_DATA_SUBSTITUTE

if TYPE_CHECKING:
    from typing import Any, Dict, List, Mapping, Optional


__all__ = [
    "DataCollection",
    "KeyValueCollectionBehavior",
    "GenAICollection",
    "HttpHeadersCollection",
    "SENSITIVE_DENYLIST",
    "EXTENDED_GDPR_DENYLIST",
]


#: Body type identifiers accepted by ``DataCollection.http_bodies``. These match
#: the spec's camelCase string values so configuration is portable across SDKs.
BODY_TYPE_INCOMING_REQUEST = "incomingRequest"
BODY_TYPE_OUTGOING_REQUEST = "outgoingRequest"
BODY_TYPE_INCOMING_RESPONSE = "incomingResponse"
BODY_TYPE_OUTGOING_RESPONSE = "outgoingResponse"

#: All valid body types. ``http_bodies`` defaults to this (collect everything the
#: platform supports); an empty list is the explicit opt-out.
ALL_BODY_TYPES = [
    BODY_TYPE_INCOMING_REQUEST,
    BODY_TYPE_OUTGOING_REQUEST,
    BODY_TYPE_INCOMING_RESPONSE,
    BODY_TYPE_OUTGOING_RESPONSE,
]

#: Default number of source lines captured above and below a stack frame.
DEFAULT_FRAME_CONTEXT_LINES = 5

#: Collection modes for key-value data (cookies, headers, query params).
COLLECTION_OFF = "off"
COLLECTION_DENYLIST = "denyList"
COLLECTION_ALLOWLIST = "allowList"
_VALID_MODES = (COLLECTION_OFF, COLLECTION_DENYLIST, COLLECTION_ALLOWLIST)

#: Canonical sensitive denylist from the spec. Values of keys that contain any of
#: these terms (partial, case-insensitive) are always replaced with
#: ``"[Filtered]"`` regardless of the configured collection mode.
SENSITIVE_DENYLIST = [
    "auth",
    "token",
    "secret",
    "password",
    "passwd",
    "pwd",
    "key",
    "jwt",
    "bearer",
    "sso",
    "saml",
    "csrf",
    "xsrf",
    "credentials",
    "session",
    "sid",
    "identity",
]

#: Additional GDPR-sensitive terms users may opt into via custom deny terms.
#: Not applied automatically; documented here for convenience.
EXTENDED_GDPR_DENYLIST = ["forwarded", "-ip", "remote-", "via", "-user"]


class KeyValueCollectionBehavior:
    """
    Controls which *values* of key-value data (cookies, headers, query params)
    are sent in plaintext versus replaced with ``"[Filtered]"``. Key names are
    always retained.

    :param mode: one of ``"off"``, ``"denyList"`` (default), ``"allowList"``.
    :param terms: deny or allow terms (depending on ``mode``) that extend the
        built-in sensitive denylist. Matched as a partial, case-insensitive
        substring of the key name.
    """

    __slots__ = ("mode", "terms")

    def __init__(self, mode: str = "denyList", terms: "Optional[List[str]]" = None):
        if mode not in _VALID_MODES:
            raise ValueError(
                "Invalid KeyValueCollectionBehavior mode {!r}. Must be one of {}.".format(
                    mode, _VALID_MODES
                )
            )
        self.mode = mode
        self.terms: "List[str]" = list(terms) if terms else []

    def __repr__(self) -> str:
        return "KeyValueCollectionBehavior(mode={!r}, terms={!r})".format(
            self.mode, self.terms
        )


class GenAICollection:
    """
    Controls capture of generative AI input and output *content*. Metadata such
    as model name and token counts is always collected regardless of these
    settings.
    """

    __slots__ = ("inputs", "outputs")

    def __init__(self, inputs: bool = True, outputs: bool = True):
        self.inputs = inputs
        self.outputs = outputs

    def __repr__(self) -> str:
        return "GenAICollection(inputs={!r}, outputs={!r})".format(
            self.inputs, self.outputs
        )


class HttpHeadersCollection:
    """
    Configures request and response header collection independently. Each
    direction is a :class:`KeyValueCollectionBehavior`.
    """

    __slots__ = ("request", "response")

    def __init__(
        self,
        request: "Optional[KeyValueCollectionBehavior]" = None,
        response: "Optional[KeyValueCollectionBehavior]" = None,
    ):
        self.request: "KeyValueCollectionBehavior" = (
            request if request is not None else KeyValueCollectionBehavior()
        )
        self.response: "KeyValueCollectionBehavior" = (
            response if response is not None else KeyValueCollectionBehavior()
        )

    def __repr__(self) -> str:
        return "HttpHeadersCollection(request={!r}, response={!r})".format(
            self.request, self.response
        )


class DataCollection:
    """
    The ``data_collection`` client option.

    Pass an instance to ``sentry_sdk.init(data_collection=...)``. Any field left
    as ``None`` is filled in with its spec default during resolution (see
    :func:`resolve_data_collection`). After resolution the instance stored on the
    client has concrete values for every field.

    :param user_info: automatically populate ``user.*`` fields (id, email,
        username, ip_address) from instrumentation. Default ``True``.
    :param cookies: cookie collection behavior. Default ``denyList``.
    :param http_headers: request/response header collection. Default
        ``denyList`` for both directions.
    :param http_bodies: list of body types to collect. ``None`` -> all valid
        types; ``[]`` -> off.
    :param query_params: URL query parameter collection. Default ``denyList``.
    :param gen_ai: generative AI input/output content collection. Default both
        ``True``.
    :param stack_frame_variables: include local variable values in stack frames.
        Default ``True`` (falls back to ``include_local_variables``).
    :param frame_context_lines: number of source lines above/below each frame.
        Default ``5`` (falls back to ``include_source_context``).
    """

    __slots__ = (
        "user_info",
        "cookies",
        "http_headers",
        "http_bodies",
        "query_params",
        "gen_ai",
        "stack_frame_variables",
        "frame_context_lines",
        "explicit",
    )

    def __init__(
        self,
        user_info: bool = True,
        cookies: "Optional[KeyValueCollectionBehavior]" = None,
        http_headers: "Optional[HttpHeadersCollection]" = None,
        http_bodies: "Optional[List[str]]" = None,
        query_params: "Optional[KeyValueCollectionBehavior]" = None,
        gen_ai: "Optional[GenAICollection]" = None,
        stack_frame_variables: "Optional[bool]" = None,
        frame_context_lines: "Optional[int]" = None,
    ):
        # Fields with no legacy fallback default to their spec value, so they are
        # always concrete (never None) on a constructed instance.
        self.user_info = user_info
        self.cookies = cookies if cookies is not None else KeyValueCollectionBehavior()
        self.http_headers = (
            http_headers if http_headers is not None else HttpHeadersCollection()
        )
        # http_bodies is None == "all valid types"; [] == off.
        self.http_bodies = http_bodies
        self.query_params = (
            query_params if query_params is not None else KeyValueCollectionBehavior()
        )
        self.gen_ai = gen_ai if gen_ai is not None else GenAICollection()
        # Frame fields keep None as "inherit from include_local_variables /
        # include_source_context" so resolution can apply the legacy fallback.
        self.stack_frame_variables = stack_frame_variables
        self.frame_context_lines = frame_context_lines
        # Whether the user supplied ``data_collection`` explicitly. Set during
        # resolution. Collection-time filtering only changes from legacy behavior
        # when this is True.
        self.explicit: bool = False

    def __repr__(self) -> str:
        return (
            "DataCollection(user_info={!r}, cookies={!r}, http_headers={!r}, "
            "http_bodies={!r}, query_params={!r}, gen_ai={!r}, "
            "stack_frame_variables={!r}, frame_context_lines={!r}, explicit={!r})"
        ).format(
            self.user_info,
            self.cookies,
            self.http_headers,
            self.http_bodies,
            self.query_params,
            self.gen_ai,
            self.stack_frame_variables,
            self.frame_context_lines,
            self.explicit,
        )


def is_sensitive_key(key: str, extra_terms: "Optional[List[str]]" = None) -> bool:
    """
    Return whether ``key`` matches the sensitive denylist using a partial,
    case-insensitive substring match.

    :param extra_terms: additional deny terms (e.g. user-provided) to consider
        alongside the built-in :data:`SENSITIVE_DENYLIST`.
    """
    lowered = key.lower()
    for term in SENSITIVE_DENYLIST:
        if term in lowered:
            return True
    if extra_terms:
        for term in extra_terms:
            if term and term.lower() in lowered:
                return True
    return False


def apply_key_value_collection(
    items: "Mapping[str, Any]",
    behavior: "KeyValueCollectionBehavior",
    substitute: "Any" = SENSITIVE_DATA_SUBSTITUTE,
) -> "Dict[str, Any]":
    """
    Apply a :class:`KeyValueCollectionBehavior` to a mapping of key-value pairs.

    Returns a new dict. Key names are always retained (except for ``off`` mode,
    which collects nothing). Sensitive keys (built-in denylist) are always
    scrubbed, even under ``allowList`` mode.
    """
    if behavior.mode == COLLECTION_OFF:
        return {}

    result: "Dict[str, Any]" = {}

    if behavior.mode == COLLECTION_ALLOWLIST:
        # behavior.terms is the ALLOW list here (not deny terms). A key sends its
        # real value only if it matches an allow term AND is not sensitive (the
        # built-in sensitive denylist always wins, even for allow-listed keys).
        for key, value in items.items():
            allowed = False
            if isinstance(key, str):
                lowered = key.lower()
                allowed = any(
                    term and term.lower() in lowered for term in behavior.terms
                )
            if allowed and not is_sensitive_key(key):
                result[key] = value
            else:
                result[key] = substitute
        return result

    # denyList (default): collect everything, scrub sensitive values.
    for key, value in items.items():
        if isinstance(key, str) and is_sensitive_key(key, behavior.terms):
            result[key] = substitute
        else:
            result[key] = value
    return result


#: Header names whose raw value must never be sent. Cookies are collected
#: separately as parsed key-value pairs (see the cookies option); the raw
#: Cookie/Set-Cookie header value is always filtered (spec: unfiltered raw cookie
#: header values MUST NOT be sent).
_ALWAYS_FILTERED_HEADERS = ("cookie", "set-cookie")


def filter_request_headers(
    headers: "Mapping[str, Any]",
    behavior: "KeyValueCollectionBehavior",
    substitute: "Any" = SENSITIVE_DATA_SUBSTITUTE,
) -> "Dict[str, Any]":
    """
    Apply a header :class:`KeyValueCollectionBehavior`, additionally always
    filtering the raw Cookie/Set-Cookie header values.
    """
    filtered = apply_key_value_collection(headers, behavior, substitute=substitute)
    for key in filtered:
        if isinstance(key, str) and key.lower() in _ALWAYS_FILTERED_HEADERS:
            filtered[key] = substitute
    return filtered


def scrub_query_string(
    query_string: str,
    behavior: "KeyValueCollectionBehavior",
) -> "Optional[str]":
    """
    Apply a query-param :class:`KeyValueCollectionBehavior` to a raw query
    string.

    Returns ``None`` when the mode is ``off`` (do not collect the query string at
    all), the scrubbed query string otherwise. An unparseable query string is
    replaced entirely with ``"[Filtered]"``.
    """
    if behavior.mode == COLLECTION_OFF:
        return None

    try:
        pairs = parse_qsl(query_string, keep_blank_values=True)
    except Exception:
        return SENSITIVE_DATA_SUBSTITUTE

    if not pairs:
        return query_string

    scrubbed = []
    for key, value in pairs:
        if behavior.mode == COLLECTION_ALLOWLIST:
            allowed = any(
                term and term.lower() in key.lower() for term in behavior.terms
            )
            scrubbed.append(
                (
                    key,
                    value
                    if (allowed and not is_sensitive_key(key))
                    else SENSITIVE_DATA_SUBSTITUTE,
                )
            )
        else:  # denyList
            scrubbed.append(
                (
                    key,
                    SENSITIVE_DATA_SUBSTITUTE
                    if is_sensitive_key(key, behavior.terms)
                    else value,
                )
            )
    return urlencode(scrubbed)


def should_collect_body_type(
    data_collection: "DataCollection",
    body_type: str,
) -> bool:
    """Return whether the given body type should be collected."""
    bodies = data_collection.http_bodies
    if bodies is None:
        return True
    return body_type in bodies


def _map_from_send_default_pii(
    send_default_pii: bool,
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Build a fully-resolved :class:`DataCollection` that mirrors the data
    ``send_default_pii`` collects today. Used when ``data_collection`` is not
    provided explicitly (resolution cases B and C).
    """
    resolved = DataCollection(
        user_info=send_default_pii,
        cookies=KeyValueCollectionBehavior(
            COLLECTION_DENYLIST if send_default_pii else COLLECTION_OFF
        ),
        # Headers are collected in both PII modes today (sensitive ones filtered
        # when PII is off), so this never maps to "off".
        http_headers=HttpHeadersCollection(),
        # Bodies are collected regardless of PII today, bounded by
        # ``max_request_body_size``.
        http_bodies=list(ALL_BODY_TYPES),
        query_params=KeyValueCollectionBehavior(
            COLLECTION_DENYLIST if send_default_pii else COLLECTION_OFF
        ),
        gen_ai=GenAICollection(inputs=send_default_pii, outputs=send_default_pii),
        stack_frame_variables=include_local_variables,
        frame_context_lines=(
            DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        ),
    )
    resolved.explicit = False
    return resolved


def _resolve_explicit(
    user_dc: "DataCollection",
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Fill in any omitted fields of a user-supplied ``DataCollection`` with their
    spec defaults (resolution case A). Frame fields fall back to the legacy
    ``include_local_variables`` / ``include_source_context`` options when unset.
    """
    # frame_context_lines accepts an integer or a boolean fallback (spec: True
    # -> platform default of 5, False -> 0). bool is a subclass of int, so
    # coerce explicitly before treating it as a line count.
    frame_context_lines = user_dc.frame_context_lines
    if frame_context_lines is None:
        frame_context_lines = (
            DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        )
    elif isinstance(frame_context_lines, bool):
        frame_context_lines = DEFAULT_FRAME_CONTEXT_LINES if frame_context_lines else 0

    resolved = DataCollection(
        # These fields are always concrete on a constructed DataCollection.
        user_info=user_dc.user_info,
        cookies=user_dc.cookies,
        http_headers=user_dc.http_headers,
        query_params=user_dc.query_params,
        gen_ai=user_dc.gen_ai,
        # http_bodies: None means "all valid types"; materialize for clarity.
        http_bodies=(
            list(user_dc.http_bodies)
            if user_dc.http_bodies is not None
            else list(ALL_BODY_TYPES)
        ),
        # Frame fields fall back to the legacy options when unset.
        stack_frame_variables=(
            user_dc.stack_frame_variables
            if user_dc.stack_frame_variables is not None
            else include_local_variables
        ),
        frame_context_lines=frame_context_lines,
    )
    resolved.explicit = True
    return resolved


def _data_collection_from_dict(d: "Dict[str, Any]") -> "DataCollection":
    """Convert a plain dict into a :class:`DataCollection`."""
    kwargs: "Dict[str, Any]" = {}

    if "user_info" in d:
        kwargs["user_info"] = d["user_info"]
    if "cookies" in d:
        kwargs["cookies"] = _kvcb_from_value(d["cookies"])
    if "http_headers" in d:
        kwargs["http_headers"] = _http_headers_from_value(d["http_headers"])
    if "http_bodies" in d:
        kwargs["http_bodies"] = d["http_bodies"]
    if "query_params" in d:
        kwargs["query_params"] = _kvcb_from_value(d["query_params"])
    if "gen_ai" in d:
        kwargs["gen_ai"] = _gen_ai_from_value(d["gen_ai"])
    if "stack_frame_variables" in d:
        kwargs["stack_frame_variables"] = d["stack_frame_variables"]
    if "frame_context_lines" in d:
        kwargs["frame_context_lines"] = d["frame_context_lines"]

    return DataCollection(**kwargs)


def _kvcb_from_value(val: "Any") -> "KeyValueCollectionBehavior":
    """Coerce a string or dict to :class:`KeyValueCollectionBehavior`."""
    if isinstance(val, KeyValueCollectionBehavior):
        return val
    if isinstance(val, str):
        return KeyValueCollectionBehavior(mode=val)
    if isinstance(val, dict):
        return KeyValueCollectionBehavior(**val)
    raise TypeError(
        "Expected a KeyValueCollectionBehavior, string, or dict, got {!r}".format(
            type(val).__name__
        )
    )


def _http_headers_from_value(val: "Any") -> "HttpHeadersCollection":
    """Coerce a dict to :class:`HttpHeadersCollection`."""
    if isinstance(val, HttpHeadersCollection):
        return val
    if isinstance(val, dict):
        kwargs: "Dict[str, Any]" = {}
        if "request" in val:
            kwargs["request"] = _kvcb_from_value(val["request"])
        if "response" in val:
            kwargs["response"] = _kvcb_from_value(val["response"])
        return HttpHeadersCollection(**kwargs)
    raise TypeError(
        "Expected an HttpHeadersCollection or dict, got {!r}".format(type(val).__name__)
    )


def _gen_ai_from_value(val: "Any") -> "GenAICollection":
    """Coerce a dict to :class:`GenAICollection`."""
    if isinstance(val, GenAICollection):
        return val
    if isinstance(val, dict):
        return GenAICollection(**val)
    raise TypeError(
        "Expected a GenAICollection or dict, got {!r}".format(type(val).__name__)
    )


def resolve_data_collection(options: "Dict[str, Any]") -> "DataCollection":
    """
    Resolve the effective :class:`DataCollection` from client ``options``.

    Reads ``data_collection``, ``send_default_pii``, ``include_local_variables``
    and ``include_source_context`` and returns a fully-resolved instance with
    concrete values for every field.

    ``data_collection`` may be a :class:`DataCollection` instance or a plain
    ``dict`` (which is converted automatically).
    """
    user_dc = options.get("data_collection")
    send_default_pii = options.get("send_default_pii")
    include_local_variables = options.get("include_local_variables")
    if include_local_variables is None:
        include_local_variables = True
    include_source_context = options.get("include_source_context")
    if include_source_context is None:
        include_source_context = True

    if user_dc is not None:
        if isinstance(user_dc, dict):
            user_dc = _data_collection_from_dict(user_dc)
        elif not isinstance(user_dc, DataCollection):
            raise TypeError(
                "`data_collection` must be a dict or sentry_sdk.DataCollection "
                "instance, got {!r}.".format(type(user_dc).__name__)
            )
        if send_default_pii is not None:
            warnings.warn(
                "`send_default_pii` is deprecated and ignored when "
                "`data_collection` is set. `data_collection` is the single "
                "source of truth for automatic data collection.",
                DeprecationWarning,
                stacklevel=2,
            )
        return _resolve_explicit(
            user_dc, include_local_variables, include_source_context
        )

    return _map_from_send_default_pii(
        bool(send_default_pii), include_local_variables, include_source_context
    )


#: Safe default used by non-recording clients: collect nothing PII-gated.
#: This is a shared, process-wide singleton. Treat it as read-only — do not
#: mutate the returned ``DataCollection`` or its nested config objects.
OFF_DATA_COLLECTION = _map_from_send_default_pii(False, True, True)
