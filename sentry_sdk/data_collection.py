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

Resolution precedence (see :func:`_resolve_data_collection`):

* ``data_collection`` set, ``send_default_pii`` unset -> honour ``data_collection``
  using the spec defaults for any omitted field.
* ``send_default_pii`` set, ``data_collection`` unset -> derive a
  resolved ``DataCollection`` that mirrors what ``send_default_pii`` collects today.
* neither set -> treated as ``send_default_pii=False``.
* both set -> ``data_collection`` wins (it is the single source of truth); a
  ``DeprecationWarning`` is emitted for ``send_default_pii``.
"""

import warnings
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Any, Dict, Literal

    from sentry_sdk._types import (
        DataCollection,
        GenAICollectionBehaviour,
        GraphQLCollectionBehaviour,
        HttpHeadersCollectionBehaviour,
        KeyValueCollectionBehaviour,
    )

# ``http_bodies`` defaults to this (collect everything the
# platform supports); an empty list is the explicit opt-out.
# response bodyies are not included here because we don't
# currently capture them (as of Jul 7 2026)
_ALL_HTTP_BODY_TYPES = [
    "incoming_request",
    "outgoing_request",
]

# Default number of source lines captured above and below a stack frame.
_DEFAULT_FRAME_CONTEXT_LINES = 5

# Collection modes for key-value data (cookies, headers, query params).
# snake_case (Python-only deviation from the spec's camelCase); never
# serialized to Sentry.
_VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES = ("off", "denylist", "allowlist")

# Values of keys that contain any of
# these terms (partial, case-insensitive) are always replaced with
# ``"[Filtered]"`` regardless of the configured collection mode.
_SENSITIVE_DENYLIST = [
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


def _map_from_send_default_pii(
    *,
    send_default_pii: bool,
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Build a fully-resolved ``DataCollection`` dict that mirrors the data
    ``send_default_pii`` collects today. Used when ``data_collection`` is not
    provided explicitly.
    """
    kv_mode: "Literal['denylist', 'off']" = "denylist" if send_default_pii else "off"
    terms = [] if send_default_pii else ["forwarded", "-ip", "remote-", "via", "-user"]

    return {
        "provided_by_user": False,
        "user_info": send_default_pii,
        "cookies": {"mode": kv_mode, "terms": terms},
        # Headers are collected in both PII modes today (sensitive ones filtered
        # when PII is off), so this never maps to "off".
        "http_headers": {
            "request": {"mode": "denylist", "terms": terms},
        },
        # Bodies are collected regardless of PII today, bounded by
        # ``max_request_body_size``.
        "http_bodies": list(_ALL_HTTP_BODY_TYPES),
        "query_params": {"mode": kv_mode, "terms": terms},
        "graphql": {"document": send_default_pii, "variables": send_default_pii},
        "gen_ai": {"inputs": send_default_pii, "outputs": send_default_pii},
        "database_query_data": send_default_pii,
        "queues": send_default_pii,
        "stack_frame_variables": include_local_variables,
        "frame_context_lines": (
            _DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        ),
    }


def _resolve_explicit(
    d: "dict[str, Any]",
    include_local_variables: bool,
    include_source_context: bool,
) -> "DataCollection":
    """
    Build a fully-resolved ``DataCollection`` from a user-supplied
    ``data_collection`` dict, filling in spec defaults for any omitted or
    partially-specified field. Frame fields fall back to the legacy
    ``include_local_variables`` / ``include_source_context`` options when unset.
    """
    # frame_context_lines accepts an integer or a boolean fallback (spec: True
    # -> platform default of 5, False -> 0). bool is a subclass of int, so
    # coerce explicitly before treating it as a line count.
    frame_context_lines = d.get("frame_context_lines")
    if frame_context_lines is None:
        frame_context_lines = (
            _DEFAULT_FRAME_CONTEXT_LINES if include_source_context else 0
        )
    elif isinstance(frame_context_lines, bool):
        frame_context_lines = _DEFAULT_FRAME_CONTEXT_LINES if frame_context_lines else 0

    stack_frame_variables = d.get("stack_frame_variables")
    if stack_frame_variables is None:
        stack_frame_variables = include_local_variables

    # http_bodies: omitted means "all valid types"; [] is the explicit opt-out.
    http_bodies = d.get("http_bodies")
    http_bodies = (
        list(http_bodies) if http_bodies is not None else list(_ALL_HTTP_BODY_TYPES)
    )

    return {
        "provided_by_user": True,
        "user_info": d.get("user_info", True),
        "cookies": _kvcb_from_value(d.get("cookies") or {}),
        "http_headers": _http_headers_from_value(d.get("http_headers") or {}),
        "http_bodies": http_bodies,
        "query_params": _kvcb_from_value(d.get("query_params") or {}),
        "graphql": _graphql_from_value(d.get("graphql") or {}),
        "gen_ai": _gen_ai_from_value(d.get("gen_ai") or {}),
        "database_query_data": d.get("database_query_data", True),
        "queues": d.get("queues", True),
        "stack_frame_variables": stack_frame_variables,
        "frame_context_lines": frame_context_lines,
    }


def _kvcb_from_value(
    val: "dict[str, Any]",
) -> "KeyValueCollectionBehaviour":
    mode = val.get("mode", "denylist")
    terms = val.get("terms", None)

    if mode not in _VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES:
        raise ValueError(
            "Invalid collection mode {!r}. Must be one of {}.".format(
                mode, _VALID_KEY_VALUE_COLLECTION_BEHAVIOUR_MODES
            )
        )

    behaviour: "dict[str, Any]" = {"mode": mode}
    if terms is not None:
        behaviour["terms"] = list(terms)
    return cast("KeyValueCollectionBehaviour", behaviour)


def _http_headers_from_value(
    val: "dict[str, Any]",
) -> "HttpHeadersCollectionBehaviour":
    return {
        "request": (
            _kvcb_from_value(val["request"])
            if "request" in val
            else _kvcb_from_value({"mode": "denylist"})
        ),
    }


def _gen_ai_from_value(val: "dict[str, Any]") -> "GenAICollectionBehaviour":
    return {
        "inputs": val.get("inputs", True),
        "outputs": val.get("outputs", True),
    }


def _graphql_from_value(
    val: "dict[str, Any]",
) -> "GraphQLCollectionBehaviour":
    return {
        "document": val.get("document", True),
        "variables": val.get("variables", True),
    }


def _resolve_data_collection(options: "Dict[str, Any]") -> "DataCollection":
    """
    Resolve the effective ``DataCollection`` dict from client ``options``.

    Reads ``data_collection``, ``send_default_pii``, ``include_local_variables``
    and ``include_source_context`` and returns a fully-resolved dict with
    concrete values for every field.

    ``data_collection`` must be a plain ``dict``.
    """
    user_dc = options.get("_experiments", {}).get("data_collection")
    send_default_pii = options.get("send_default_pii")

    include_local_variables = (
        bool(options.get("include_local_variables"))
        if options.get("include_local_variables") is not None
        else True
    )
    include_source_context = (
        bool(options.get("include_source_context"))
        if options.get("include_source_context") is not None
        else True
    )

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
                "`data_collection` is set.",
                DeprecationWarning,
                stacklevel=2,
            )
        return _resolve_explicit(
            user_dc,
            include_local_variables,
            include_source_context,
        )

    return _map_from_send_default_pii(
        send_default_pii=bool(send_default_pii),
        include_local_variables=include_local_variables,
        include_source_context=include_source_context,
    )
