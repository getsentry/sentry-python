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
  resolved ``DataCollection`` that mirrors what ``send_default_pii`` collects today.
* neither set -> treated as ``send_default_pii=False``.
* both set -> ``data_collection`` wins (it is the single source of truth); a
  ``DeprecationWarning`` is emitted for ``send_default_pii``.

The new collection-time filtering mechanisms (the partial-match sensitive
denylist and allow/deny key-value modes) only become active when
``data_collection`` is provided explicitly. Otherwise the SDK keeps its existing
behaviour so that upgrading without configuring ``data_collection`` changes
nothing.
"""

import warnings
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qsl, urlencode

from sentry_sdk._types import SENSITIVE_DATA_SUBSTITUTE

if TYPE_CHECKING:
    from typing import Any, Dict, List, Mapping, Optional

    from sentry_sdk._types import (
        DatabaseCollectionBehaviour,
        DataCollection,
        DataCollectionUserOptions,
        GenAICollectionBehaviour,
        GraphQLCollectionBehaviour,
        HttpHeadersCollectionBehaviour,
        KeyValueCollectionBehaviour,
    )

#: All valid body types. ``http_bodies`` defaults to this (collect everything the
#: platform supports); an empty list is the explicit opt-out.
ALL_HTTP_BODY_TYPES = [
    "incoming_request",
    "outgoing_request",
    "incoming_response",
    "outgoing_response",
]

#: Default number of source lines captured above and below a stack frame.
DEFAULT_FRAME_CONTEXT_LINES = 5

#: Collection modes for key-value data (cookies, headers, query params).
#: snake_case (Python-only deviation from the spec's camelCase); never
#: serialized to Sentry.
_VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES = ("off", "deny_list", "allow_list")

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
    behaviour: "KeyValueCollectionBehaviour",
    substitute: "Any" = SENSITIVE_DATA_SUBSTITUTE,
) -> "Dict[str, Any]":
    """
    Apply a :class:`KeyValueCollectionBehaviour` to a mapping of key-value pairs.

    Returns a new dict. Key names are always retained (except for ``off`` mode,
    which collects nothing). Sensitive keys (built-in denylist) are always
    scrubbed, even under ``allow_list`` mode.
    """
    mode = behaviour.get("mode", "deny_list")
    terms = behaviour.get("terms") or []

    if mode == "off":
        return {}

    result: "Dict[str, Any]" = {}

    if mode == "allow_list":
        # ``terms`` is the ALLOW list here (not deny terms). A key sends its
        # real value only if it matches an allow term AND is not sensitive (the
        # built-in sensitive denylist always wins, even for allow-listed keys).
        for key, value in items.items():
            allowed = False
            if isinstance(key, str):
                lowered = key.lower()
                allowed = any(term and term.lower() in lowered for term in terms)
            if allowed and not is_sensitive_key(key):
                result[key] = value
            else:
                result[key] = substitute
        return result

    # deny_list (default): collect everything, scrub sensitive values.
    for key, value in items.items():
        if isinstance(key, str) and is_sensitive_key(key, terms):
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
    behaviour: "KeyValueCollectionBehaviour",
    substitute: "Any" = SENSITIVE_DATA_SUBSTITUTE,
) -> "Dict[str, Any]":
    """
    Apply a header :class:`KeyValueCollectionBehaviour`, additionally always
    filtering the raw Cookie/Set-Cookie header values.
    """
    filtered = apply_key_value_collection(headers, behaviour, substitute=substitute)
    for key in filtered:
        if isinstance(key, str) and key.lower() in _ALWAYS_FILTERED_HEADERS:
            filtered[key] = substitute
    return filtered


def scrub_query_string(
    query_string: str,
    behaviour: "KeyValueCollectionBehaviour",
) -> "Optional[str]":
    """
    Apply a query-param :class:`KeyValueCollectionBehaviour` to a raw query
    string.

    Returns ``None`` when the mode is ``off`` (do not collect the query string at
    all), the scrubbed query string otherwise. An unparseable query string is
    replaced entirely with ``"[Filtered]"``.
    """
    mode = behaviour.get("mode", "deny_list")
    terms = behaviour.get("terms") or []

    if mode == "off":
        return None

    try:
        pairs = parse_qsl(query_string, keep_blank_values=True)
    except Exception:
        return SENSITIVE_DATA_SUBSTITUTE

    if not pairs:
        return query_string

    scrubbed = []
    for key, value in pairs:
        if mode == "allow_list":
            allowed = any(term and term.lower() in key.lower() for term in terms)
            scrubbed.append(
                (
                    key,
                    value
                    if (allowed and not is_sensitive_key(key))
                    else SENSITIVE_DATA_SUBSTITUTE,
                )
            )
        else:  # deny_list
            scrubbed.append(
                (
                    key,
                    SENSITIVE_DATA_SUBSTITUTE
                    if is_sensitive_key(key, terms)
                    else value,
                )
            )
    return urlencode(scrubbed)


def should_collect_body_type(
    data_collection: "DataCollection",
    body_type: str,
) -> bool:
    """Return whether the given body type should be collected."""
    bodies = data_collection.get("http_bodies")
    if bodies is None:
        return True
    return body_type in bodies


def _map_from_send_default_pii(
    send_default_pii: bool,
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Build a fully-resolved ``DataCollection`` dict that mirrors the data
    ``send_default_pii`` collects today. Used when ``data_collection`` is not
    provided explicitly.

    PII-bearing content gates on ``send_default_pii``: ``graphql.variables`` and
    ``database.query_params`` follow it, while ``graphql.document`` stays ``True``.
    """
    kv_mode = "deny_list" if send_default_pii else "off"  # type: Literal["off", "deny_list", "allow_list"]
    return {
        "provided_by_user": False,
        "user_info": send_default_pii,
        "cookies": {"mode": kv_mode},
        # Headers are collected in both PII modes today (sensitive ones filtered
        # when PII is off), so this never maps to "off".
        "http_headers": {
            "request": {"mode": "deny_list"},
            "response": {"mode": "deny_list"},
        },
        # Bodies are collected regardless of PII today, bounded by
        # ``max_request_body_size``.
        "http_bodies": list(ALL_HTTP_BODY_TYPES),
        "query_params": {"mode": kv_mode},
        "graphql": {"document": True, "variables": send_default_pii},
        "gen_ai": {"inputs": send_default_pii, "outputs": send_default_pii},
        "database": {"query_params": send_default_pii},
        "stack_frame_variables": include_local_variables,
        "frame_context_lines": (
            DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        ),
    }


def _resolve_explicit(
    user_dc: "DataCollectionUserOptions",
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Fill in any omitted fields of a user-supplied ``DataCollection`` dict with
    their spec defaults. Frame fields fall back to the legacy
    ``include_local_variables`` / ``include_source_context`` options when unset.
    """
    # frame_context_lines accepts an integer or a boolean fallback (spec: True
    # -> platform default of 5, False -> 0). bool is a subclass of int, so
    # coerce explicitly before treating it as a line count.
    frame_context_lines = user_dc.get("frame_context_lines")
    if frame_context_lines is None:
        frame_context_lines = (
            DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        )
    elif isinstance(frame_context_lines, bool):
        frame_context_lines = DEFAULT_FRAME_CONTEXT_LINES if frame_context_lines else 0

    stack_frame_variables = user_dc.get("stack_frame_variables")
    if stack_frame_variables is None:
        stack_frame_variables = include_local_variables

    # http_bodies: omitted means "all valid types"; [] is the explicit opt-out.
    http_bodies = user_dc.get("http_bodies")
    http_bodies = (
        list(http_bodies) if http_bodies is not None else list(ALL_HTTP_BODY_TYPES)
    )

    return {
        "provided_by_user": True,
        "user_info": user_dc.get("user_info", True),
        "cookies": user_dc.get("cookies") or _kvcb_from_value("deny_list"),
        "http_headers": user_dc.get("http_headers")
        or _http_headers_from_value("deny_list"),
        "http_bodies": http_bodies,
        "query_params": user_dc.get("query_params") or _kvcb_from_value("deny_list"),
        "graphql": user_dc.get("graphql") or _graphql_from_value({}),
        "gen_ai": user_dc.get("gen_ai") or _gen_ai_from_value({}),
        "database": user_dc.get("database") or _database_from_value({}),
        "stack_frame_variables": stack_frame_variables,
        "frame_context_lines": frame_context_lines,
    }


def _data_collection_from_dict(d: "Dict[str, Any]") -> "DataCollectionUserOptions":
    """
    Normalize only the keys the user supplied into a partial
    ``DataCollectionUserOptions`` dict. Nested config values are coerced (and
    their own defaults filled) by the per-field helpers.
    """
    result: "DataCollectionUserOptions" = {}

    if "user_info" in d:
        result["user_info"] = d["user_info"]
    if "cookies" in d:
        result["cookies"] = _kvcb_from_value(d["cookies"])
    if "http_headers" in d:
        result["http_headers"] = _http_headers_from_value(d["http_headers"])
    if "http_bodies" in d:
        result["http_bodies"] = d["http_bodies"]
    if "query_params" in d:
        result["query_params"] = _kvcb_from_value(d["query_params"])
    if "graphql" in d:
        result["graphql"] = _graphql_from_value(d["graphql"])
    if "gen_ai" in d:
        result["gen_ai"] = _gen_ai_from_value(d["gen_ai"])
    if "database" in d:
        result["database"] = _database_from_value(d["database"])
    if "stack_frame_variables" in d:
        result["stack_frame_variables"] = d["stack_frame_variables"]
    if "frame_context_lines" in d:
        result["frame_context_lines"] = d["frame_context_lines"]

    return result


def _kvcb_from_value(val: "Any") -> "KeyValueCollectionBehaviour":
    """
    Coerce a string or dict to a ``KeyValueCollectionBehaviour`` dict, defaulting
    ``mode`` to ``deny_list`` and validating it against the known modes.
    """
    if isinstance(val, str):
        mode = val
        terms = None
    elif isinstance(val, dict):
        mode = val.get("mode", "deny_list")
        terms = val.get("terms")
    else:
        raise TypeError(
            "Expected a string or dict for key-value collection behaviour, "
            "got {!r}".format(type(val).__name__)
        )

    if mode not in _VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES:
        raise ValueError(
            "Invalid collection mode {!r}. Must be one of {}.".format(
                mode, _VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES
            )
        )

    behaviour = {"mode": mode}  # type: Dict[str, Any]
    if terms is not None:
        behaviour["terms"] = list(terms)
    return cast("KeyValueCollectionBehaviour", behaviour)


def _http_headers_from_value(val: "Any") -> "HttpHeadersCollectionBehaviour":
    """
    Coerce a value to an ``HttpHeadersCollectionBehaviour`` dict.

    Accepts ``{"request": ..., "response": ...}`` (each direction defaulting to
    ``deny_list``) or a shorthand — a string or single key-value behaviour dict —
    applied to both directions.
    """
    if isinstance(val, dict) and ("request" in val or "response" in val):
        return {
            "request": (
                _kvcb_from_value(val["request"])
                if "request" in val
                else _kvcb_from_value("deny_list")
            ),
            "response": (
                _kvcb_from_value(val["response"])
                if "response" in val
                else _kvcb_from_value("deny_list")
            ),
        }
    if isinstance(val, (str, dict)):
        # Shorthand: a single behaviour applies to both directions.
        return {
            "request": _kvcb_from_value(val),
            "response": _kvcb_from_value(val),
        }
    raise TypeError(
        "Expected a dict or string for http_headers, got {!r}".format(
            type(val).__name__
        )
    )


def _gen_ai_from_value(val: "Any") -> "GenAICollectionBehaviour":
    """Coerce a dict to a ``GenAICollectionBehaviour`` dict; ``inputs``/``outputs`` default to ``True``."""
    if not isinstance(val, dict):
        raise TypeError(
            "Expected a dict for gen_ai, got {!r}".format(type(val).__name__)
        )
    return {
        "inputs": val.get("inputs", True),
        "outputs": val.get("outputs", True),
    }


def _graphql_from_value(val: "Any") -> "GraphQLCollectionBehaviour":
    """Coerce a dict to a ``GraphQLCollectionBehaviour`` dict; ``document``/``variables`` default to ``True``."""
    if not isinstance(val, dict):
        raise TypeError(
            "Expected a dict for graphql, got {!r}".format(type(val).__name__)
        )
    return {
        "document": val.get("document", True),
        "variables": val.get("variables", True),
    }


def _database_from_value(val: "Any") -> "DatabaseCollectionBehaviour":
    """Coerce a dict to a ``DatabaseCollectionBehaviour`` dict; ``query_params`` defaults to ``True``."""
    if not isinstance(val, dict):
        raise TypeError(
            "Expected a dict for database, got {!r}".format(type(val).__name__)
        )
    return {"query_params": val.get("query_params", True)}


def resolve_data_collection(options: "Dict[str, Any]") -> "DataCollection":
    """
    Resolve the effective ``DataCollection`` dict from client ``options``.

    Reads ``data_collection``, ``send_default_pii``, ``include_local_variables``
    and ``include_source_context`` and returns a fully-resolved dict with
    concrete values for every field.

    ``data_collection`` must be a plain ``dict``.
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
        if not isinstance(user_dc, dict):
            raise TypeError(
                "`data_collection` must be a dict, got {!r}.".format(
                    type(user_dc).__name__
                )
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
            _data_collection_from_dict(user_dc),
            include_local_variables,
            include_source_context,
        )

    return _map_from_send_default_pii(
        send_default_pii=bool(send_default_pii),
        include_local_variables=include_local_variables,
        include_source_context=include_source_context,
    )
