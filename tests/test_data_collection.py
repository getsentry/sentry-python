import warnings

import pytest

import sentry_sdk
from sentry_sdk.data_collection import (
    ALL_HTTP_BODY_TYPES,
    SENSITIVE_DENYLIST,
    _http_headers_from_value,
    _kvcb_from_value,
    apply_key_value_collection,
    filter_request_headers,
    is_sensitive_key,
    resolve_data_collection,
    scrub_query_string,
    should_collect_body_type,
)

# ---------------------------------------------------------------------------
# Sensitive denylist (partial, case-insensitive)
# ---------------------------------------------------------------------------


def test_sensitive_denylist_matches_spec():
    assert SENSITIVE_DENYLIST == [
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


@pytest.mark.parametrize(
    "key,expected",
    [
        ("Authorization", True),  # contains "auth"
        ("X-Auth-Token", True),
        ("authorization", True),
        ("PASSWORD", True),
        ("X-Api-Key", True),  # contains "key"
        ("sessionid", True),  # contains "session" and "sid"
        ("Accept", False),
        ("Content-Type", False),
        ("X-Request-Id", False),
    ],
)
def test_is_sensitive_key(key, expected):
    assert is_sensitive_key(key) is expected


def test_is_sensitive_key_extra_terms():
    assert is_sensitive_key("x-forwarded-for", ["forwarded"]) is True
    assert is_sensitive_key("x-forwarded-for") is False


# ---------------------------------------------------------------------------
# Key-value collection behaviour
# ---------------------------------------------------------------------------


def test_kvcb_invalid_mode():
    with pytest.raises(ValueError):
        _kvcb_from_value({"mode": "nope"})


def test_kvcb_from_string_defaults_terms():
    assert _kvcb_from_value("allow_list") == {"mode": "allow_list"}


def test_kvcb_from_dict_defaults_mode():
    assert _kvcb_from_value({"terms": ["x"]}) == {"mode": "deny_list", "terms": ["x"]}


def test_apply_off():
    assert apply_key_value_collection({"a": "1"}, {"mode": "off"}) == {}


def test_apply_denylist_scrubs_sensitive_keeps_rest():
    items = {"Authorization": "secret", "Accept": "json", "X-Id": "1"}
    out = apply_key_value_collection(items, {"mode": "deny_list"})
    assert out == {"Authorization": "[Filtered]", "Accept": "json", "X-Id": "1"}


def test_apply_denylist_extra_terms():
    items = {"X-Custom": "v", "Accept": "json"}
    out = apply_key_value_collection(
        items, {"mode": "deny_list", "terms": ["x-custom"]}
    )
    assert out == {"X-Custom": "[Filtered]", "Accept": "json"}


def test_apply_allowlist_only_allowed_real():
    items = {"X-Request-Id": "r1", "Accept": "json", "Authorization": "x"}
    out = apply_key_value_collection(
        items, {"mode": "allow_list", "terms": ["x-request-id"]}
    )
    assert out == {
        "X-Request-Id": "r1",
        "Accept": "[Filtered]",
        "Authorization": "[Filtered]",
    }


def test_apply_allowlist_sensitive_always_scrubbed():
    # Even if a sensitive key is allow-listed, it is still scrubbed.
    items = {"Authorization": "x"}
    out = apply_key_value_collection(
        items, {"mode": "allow_list", "terms": ["authorization"]}
    )
    assert out == {"Authorization": "[Filtered]"}


def test_filter_request_headers_always_filters_cookie():
    items = {"Cookie": "a=b", "Set-Cookie": "c=d", "Accept": "json"}
    out = filter_request_headers(items, {"mode": "deny_list"})
    assert out == {
        "Cookie": "[Filtered]",
        "Set-Cookie": "[Filtered]",
        "Accept": "json",
    }


# ---------------------------------------------------------------------------
# Query string scrubbing
# ---------------------------------------------------------------------------


def test_scrub_query_off():
    assert scrub_query_string("a=1&token=x", {"mode": "off"}) is None


def test_scrub_query_denylist():
    out = scrub_query_string("token=abc&page=5", {"mode": "deny_list"})
    assert "page=5" in out
    assert "token=" in out
    assert "abc" not in out


def test_scrub_query_allowlist():
    out = scrub_query_string(
        "token=abc&page=5", {"mode": "allow_list", "terms": ["page"]}
    )
    assert "page=5" in out
    assert "abc" not in out


# ---------------------------------------------------------------------------
# Body type collection
# ---------------------------------------------------------------------------


def test_body_type_default_all():
    # An omitted http_bodies means all valid types.
    assert should_collect_body_type({}, "incoming_request") is True


def test_body_type_explicit_list():
    dc = {"http_bodies": ["incoming_request"]}
    assert should_collect_body_type(dc, "incoming_request") is True
    assert should_collect_body_type(dc, "outgoing_request") is False


def test_body_type_empty_off():
    assert should_collect_body_type({"http_bodies": []}, "incoming_request") is False


# ---------------------------------------------------------------------------
# http_headers coercion (shorthand + per-direction)
# ---------------------------------------------------------------------------


def test_http_headers_collection_defaults():
    hh = _http_headers_from_value({})
    assert hh["request"] == {"mode": "deny_list"}
    assert hh["response"] == {"mode": "deny_list"}


def test_http_headers_shorthand_string_applies_to_both():
    hh = _http_headers_from_value("off")
    assert hh["request"]["mode"] == "off"
    assert hh["response"]["mode"] == "off"


def test_http_headers_shorthand_single_kvcb_applies_to_both():
    hh = _http_headers_from_value({"mode": "allow_list", "terms": ["x-id"]})
    assert hh["request"] == {"mode": "allow_list", "terms": ["x-id"]}
    assert hh["response"] == {"mode": "allow_list", "terms": ["x-id"]}


def test_http_headers_per_direction_defaults_missing_to_denylist():
    hh = _http_headers_from_value({"request": "off"})
    assert hh["request"]["mode"] == "off"
    assert hh["response"] == {"mode": "deny_list"}


# ---------------------------------------------------------------------------
# Resolution of data_collection against the legacy send_default_pii option
# ---------------------------------------------------------------------------


def _resolve(**options):
    base = {
        "data_collection": None,
        "send_default_pii": None,
        "include_local_variables": True,
        "include_source_context": True,
    }
    base.update(options)
    return resolve_data_collection(base)


def test_resolve_no_options_collects_no_pii():
    dc = _resolve()
    assert dc["user_info"] is False
    assert dc["gen_ai"]["inputs"] is False and dc["gen_ai"]["outputs"] is False
    assert dc["cookies"]["mode"] == "off"
    assert dc["query_params"]["mode"] == "off"
    assert dc["http_headers"]["request"]["mode"] == "deny_list"
    assert dc["http_bodies"] == ALL_HTTP_BODY_TYPES
    assert dc["frame_context_lines"] == 5


def test_resolve_send_default_pii_true_collects_pii():
    dc = _resolve(send_default_pii=True)
    assert dc["user_info"] is True
    assert dc["gen_ai"]["inputs"] is True and dc["gen_ai"]["outputs"] is True
    assert dc["cookies"]["mode"] == "deny_list"
    assert dc["query_params"]["mode"] == "deny_list"


def test_resolve_send_default_pii_false_collects_no_pii():
    dc = _resolve(send_default_pii=False)
    assert dc["user_info"] is False
    assert dc["cookies"]["mode"] == "off"


def test_resolve_explicit_data_collection_uses_spec_defaults():
    dc = _resolve(data_collection={})
    # spec defaults: collect more
    assert dc["user_info"] is True
    assert dc["gen_ai"]["inputs"] is True and dc["gen_ai"]["outputs"] is True
    assert dc["cookies"]["mode"] == "deny_list"
    assert dc["query_params"]["mode"] == "deny_list"
    assert dc["http_bodies"] == ALL_HTTP_BODY_TYPES


def test_resolve_explicit_partial_fills_omitted_with_spec_defaults():
    dc = _resolve(data_collection={"user_info": False, "http_bodies": []})
    assert dc["user_info"] is False
    assert dc["http_bodies"] == []
    # omitted fields use spec defaults
    assert dc["gen_ai"]["inputs"] is True
    assert dc["cookies"]["mode"] == "deny_list"


def test_resolve_explicit_frame_fields_fall_back_to_legacy_options():
    dc = _resolve(
        data_collection={},
        include_local_variables=False,
        include_source_context=False,
    )
    assert dc["stack_frame_variables"] is False
    assert dc["frame_context_lines"] == 0


def test_resolve_data_collection_overrides_send_default_pii_and_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dc = _resolve(send_default_pii=True, data_collection={"user_info": False})
    assert dc["user_info"] is False  # data_collection wins
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_resolve_accepts_dict_with_nested_dicts():
    dc = _resolve(
        data_collection={
            "cookies": "off",
            "query_params": {"mode": "allow_list", "terms": ["page"]},
            "http_headers": {"request": "off"},
            "gen_ai": {"inputs": False, "outputs": True},
        }
    )
    assert dc["cookies"]["mode"] == "off"
    assert dc["query_params"]["mode"] == "allow_list"
    assert dc["query_params"]["terms"] == ["page"]
    assert dc["http_headers"]["request"]["mode"] == "off"
    assert dc["http_headers"]["response"]["mode"] == "deny_list"
    assert dc["gen_ai"]["inputs"] is False
    assert dc["gen_ai"]["outputs"] is True


def test_resolve_http_headers_shorthand_off_applies_to_both():
    dc = _resolve(data_collection={"http_headers": "off"})
    assert dc["http_headers"]["request"]["mode"] == "off"
    assert dc["http_headers"]["response"]["mode"] == "off"


def test_resolve_http_headers_shorthand_single_kvcb_applies_to_both():
    dc = _resolve(data_collection={"http_headers": {"mode": "off"}})
    assert dc["http_headers"]["request"]["mode"] == "off"
    assert dc["http_headers"]["response"]["mode"] == "off"


@pytest.mark.parametrize("bad", [42, "oops", ["a"]])
def test_resolve_rejects_non_dict(bad):
    with pytest.raises(TypeError):
        _resolve(data_collection=bad)


# ---------------------------------------------------------------------------
# graphql / database defaults and legacy PII gating
# ---------------------------------------------------------------------------


def test_resolve_explicit_graphql_database_defaults():
    dc = _resolve(data_collection={})
    assert dc["graphql"] == {"document": True, "variables": True}
    assert dc["database"] == {"query_params": True}


def test_resolve_legacy_pii_off_gates_graphql_and_database():
    dc = _resolve(send_default_pii=False)
    # document is always collected; variables/query_params follow send_default_pii
    assert dc["graphql"]["document"] is True
    assert dc["graphql"]["variables"] is False
    assert dc["database"]["query_params"] is False


def test_resolve_legacy_pii_on_collects_graphql_and_database():
    dc = _resolve(send_default_pii=True)
    assert dc["graphql"]["document"] is True
    assert dc["graphql"]["variables"] is True
    assert dc["database"]["query_params"] is True


def test_resolve_explicit_partial_graphql_fills_omitted():
    dc = _resolve(data_collection={"graphql": {"variables": False}})
    assert dc["graphql"]["document"] is True
    assert dc["graphql"]["variables"] is False


# ---------------------------------------------------------------------------
# frame_context_lines boolean fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(True, 5), (False, 0), (3, 3), (0, 0)],
)
def test_frame_context_lines_bool_fallback(value, expected):
    dc = _resolve(data_collection={"frame_context_lines": value})
    assert dc["frame_context_lines"] == expected


# ---------------------------------------------------------------------------
# Client accessors
# ---------------------------------------------------------------------------


def test_client_data_collection_defaults_to_no_pii():
    sentry_sdk.init()
    client = sentry_sdk.get_client()
    assert client.data_collection["user_info"] is False
    assert client.should_send_default_pii() is False
    assert client.data_collection["provided_by_user"] is False


def test_client_send_default_pii_enables_user_info():
    sentry_sdk.init(send_default_pii=True)
    client = sentry_sdk.get_client()
    assert client.data_collection["user_info"] is True
    assert client.data_collection["provided_by_user"] is False


def test_client_explicit_data_collection_overrides_user_info():
    sentry_sdk.init(data_collection={"user_info": False})
    client = sentry_sdk.get_client()
    assert client.data_collection["user_info"] is False
    assert client.data_collection["provided_by_user"] is True


def test_client_dsnless_spotlight_rederives_data_collection():
    # DSN-less spotlight flips send_default_pii on; non-explicit data_collection
    # is re-derived to agree.
    sentry_sdk.init(spotlight=True)
    client = sentry_sdk.get_client()
    assert client.data_collection["provided_by_user"] is False
    assert client.data_collection["user_info"] is True


def test_client_dsnless_spotlight_respects_explicit_data_collection():
    sentry_sdk.init(spotlight=True, data_collection={"user_info": False})
    client = sentry_sdk.get_client()
    assert client.data_collection["provided_by_user"] is True
    assert client.data_collection["user_info"] is False
