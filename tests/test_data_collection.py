import warnings

import pytest

import sentry_sdk
from sentry_sdk import (
    DataCollection,
    GenAICollection,
    HttpHeadersCollection,
    KeyValueCollectionBehavior,
)
from sentry_sdk.data_collection import (
    ALL_BODY_TYPES,
    SENSITIVE_DENYLIST,
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
# Key-value collection behavior
# ---------------------------------------------------------------------------


def test_kvcb_invalid_mode():
    with pytest.raises(ValueError):
        KeyValueCollectionBehavior(mode="nope")


def test_apply_off():
    assert (
        apply_key_value_collection({"a": "1"}, KeyValueCollectionBehavior("off")) == {}
    )


def test_apply_denylist_scrubs_sensitive_keeps_rest():
    items = {"Authorization": "secret", "Accept": "json", "X-Id": "1"}
    out = apply_key_value_collection(items, KeyValueCollectionBehavior("denyList"))
    assert out == {"Authorization": "[Filtered]", "Accept": "json", "X-Id": "1"}


def test_apply_denylist_extra_terms():
    items = {"X-Custom": "v", "Accept": "json"}
    out = apply_key_value_collection(
        items, KeyValueCollectionBehavior("denyList", ["x-custom"])
    )
    assert out == {"X-Custom": "[Filtered]", "Accept": "json"}


def test_apply_allowlist_only_allowed_real():
    items = {"X-Request-Id": "r1", "Accept": "json", "Authorization": "x"}
    out = apply_key_value_collection(
        items, KeyValueCollectionBehavior("allowList", ["x-request-id"])
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
        items, KeyValueCollectionBehavior("allowList", ["authorization"])
    )
    assert out == {"Authorization": "[Filtered]"}


def test_filter_request_headers_always_filters_cookie():
    items = {"Cookie": "a=b", "Set-Cookie": "c=d", "Accept": "json"}
    out = filter_request_headers(items, KeyValueCollectionBehavior("denyList"))
    assert out == {
        "Cookie": "[Filtered]",
        "Set-Cookie": "[Filtered]",
        "Accept": "json",
    }


# ---------------------------------------------------------------------------
# Query string scrubbing
# ---------------------------------------------------------------------------


def test_scrub_query_off():
    assert scrub_query_string("a=1&token=x", KeyValueCollectionBehavior("off")) is None


def test_scrub_query_denylist():
    out = scrub_query_string("token=abc&page=5", KeyValueCollectionBehavior("denyList"))
    assert "page=5" in out
    assert "token=" in out
    assert "abc" not in out


def test_scrub_query_allowlist():
    out = scrub_query_string(
        "token=abc&page=5", KeyValueCollectionBehavior("allowList", ["page"])
    )
    assert "page=5" in out
    assert "abc" not in out


# ---------------------------------------------------------------------------
# Body type collection
# ---------------------------------------------------------------------------


def test_body_type_default_all():
    dc = DataCollection()
    # None means all valid types
    assert should_collect_body_type(dc, "incomingRequest") is True


def test_body_type_explicit_list():
    dc = DataCollection(http_bodies=["incomingRequest"])
    assert should_collect_body_type(dc, "incomingRequest") is True
    assert should_collect_body_type(dc, "outgoingRequest") is False


def test_body_type_empty_off():
    dc = DataCollection(http_bodies=[])
    assert should_collect_body_type(dc, "incomingRequest") is False


# ---------------------------------------------------------------------------
# Resolution: cases A / B / C / D
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


def test_resolve_case_c_neither():
    dc = _resolve()
    assert dc.explicit is False
    assert dc.user_info is False
    assert dc.gen_ai.inputs is False and dc.gen_ai.outputs is False
    assert dc.cookies.mode == "off"
    assert dc.query_params.mode == "off"
    assert dc.http_headers.request.mode == "denyList"
    assert dc.http_bodies == ALL_BODY_TYPES
    assert dc.frame_context_lines == 5


def test_resolve_case_b_pii_true():
    dc = _resolve(send_default_pii=True)
    assert dc.explicit is False
    assert dc.user_info is True
    assert dc.gen_ai.inputs is True and dc.gen_ai.outputs is True
    assert dc.cookies.mode == "denyList"
    assert dc.query_params.mode == "denyList"


def test_resolve_case_b_pii_false():
    dc = _resolve(send_default_pii=False)
    assert dc.explicit is False
    assert dc.user_info is False
    assert dc.cookies.mode == "off"


def test_resolve_case_a_defaults():
    dc = _resolve(data_collection=DataCollection())
    assert dc.explicit is True
    # spec defaults: collect more
    assert dc.user_info is True
    assert dc.gen_ai.inputs is True and dc.gen_ai.outputs is True
    assert dc.cookies.mode == "denyList"
    assert dc.query_params.mode == "denyList"
    assert dc.http_bodies == ALL_BODY_TYPES


def test_resolve_case_a_partial_uses_spec_defaults_for_omitted():
    dc = _resolve(data_collection=DataCollection(user_info=False, http_bodies=[]))
    assert dc.explicit is True
    assert dc.user_info is False
    assert dc.http_bodies == []
    # omitted fields use spec defaults
    assert dc.gen_ai.inputs is True
    assert dc.cookies.mode == "denyList"


def test_resolve_case_a_frame_fallback_to_legacy_options():
    dc = _resolve(
        data_collection=DataCollection(),
        include_local_variables=False,
        include_source_context=False,
    )
    assert dc.stack_frame_variables is False
    assert dc.frame_context_lines == 0


def test_resolve_case_d_both_data_collection_wins_and_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dc = _resolve(
            send_default_pii=True, data_collection=DataCollection(user_info=False)
        )
    assert dc.explicit is True
    assert dc.user_info is False  # data_collection wins
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_resolve_accepts_dict():
    dc = _resolve(data_collection={"user_info": False, "http_bodies": []})
    assert dc.explicit is True
    assert dc.user_info is False
    assert dc.http_bodies == []
    assert dc.gen_ai.inputs is True


def test_resolve_accepts_dict_with_nested_dicts():
    dc = _resolve(
        data_collection={
            "cookies": "off",
            "query_params": {"mode": "allowList", "terms": ["page"]},
            "http_headers": {"request": "off"},
            "gen_ai": {"inputs": False, "outputs": True},
        }
    )
    assert dc.cookies.mode == "off"
    assert dc.query_params.mode == "allowList"
    assert dc.query_params.terms == ["page"]
    assert dc.http_headers.request.mode == "off"
    assert dc.http_headers.response.mode == "denyList"
    assert dc.gen_ai.inputs is False
    assert dc.gen_ai.outputs is True


def test_resolve_rejects_non_datacollection():
    with pytest.raises(TypeError):
        _resolve(data_collection=42)


# ---------------------------------------------------------------------------
# frame_context_lines boolean fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(True, 5), (False, 0), (3, 3), (0, 0)],
)
def test_frame_context_lines_bool_fallback(value, expected):
    dc = _resolve(data_collection=DataCollection(frame_context_lines=value))
    assert dc.frame_context_lines == expected


# ---------------------------------------------------------------------------
# Client accessors
# ---------------------------------------------------------------------------


def test_client_accessors_case_c():
    sentry_sdk.init()
    client = sentry_sdk.get_client()
    assert client.should_collect_user_info() is False
    assert client.should_send_default_pii() is False
    assert client.should_collect_gen_ai_inputs() is False
    assert client.should_collect_gen_ai_outputs() is False


def test_client_accessors_case_b_pii():
    sentry_sdk.init(send_default_pii=True)
    client = sentry_sdk.get_client()
    assert client.should_collect_user_info() is True
    assert client.should_collect_gen_ai_inputs() is True
    # include_prompts=False override still disables (legacy AND semantics)
    assert client.should_collect_gen_ai_inputs(False) is False
    assert client.should_collect_gen_ai_inputs(True) is True


def test_client_accessors_case_a():
    sentry_sdk.init(data_collection=DataCollection(user_info=False))
    client = sentry_sdk.get_client()
    assert client.should_collect_user_info() is False
    # gen_ai defaults to True in explicit mode
    assert client.should_collect_gen_ai_inputs() is True
    # explicit integration override wins
    assert client.should_collect_gen_ai_inputs(False) is False


def test_client_accessors_gen_ai_explicit_override():
    sentry_sdk.init(
        data_collection=DataCollection(
            gen_ai=GenAICollection(inputs=False, outputs=True)
        )
    )
    client = sentry_sdk.get_client()
    assert client.should_collect_gen_ai_inputs() is False
    assert client.should_collect_gen_ai_outputs() is True
    # integration override beats the global gen_ai setting
    assert client.should_collect_gen_ai_inputs(True) is True


def test_http_headers_collection_defaults():
    hh = HttpHeadersCollection()
    assert hh.request.mode == "denyList"
    assert hh.response.mode == "denyList"
