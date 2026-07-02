import warnings
from typing import TYPE_CHECKING

import pytest

import sentry_sdk
from sentry_sdk.data_collection import (
    ALL_HTTP_BODY_TYPES,
    resolve_data_collection,
)

if TYPE_CHECKING:
    from sentry_sdk._types import DataCollectionUserOptions


def test_kvcb_invalid_mode():
    with pytest.raises(ValueError):
        sentry_sdk.init(data_collection={"cookies": {"mode": "nope"}})  # type: ignore Purposely ignoring to test invalid option


def test_kvcb_from_dict_defaults_mode():
    sentry_sdk.init(data_collection={"cookies": {"mode": "deny_list", "terms": ["x"]}})
    client = sentry_sdk.get_client()
    assert client.data_collection["cookies"] == {"mode": "deny_list", "terms": ["x"]}


def test_http_headers_collection_defaults():
    sentry_sdk.init(data_collection={"http_headers": {}})  # type: ignore Purposely ignoring to test invalid option
    client = sentry_sdk.get_client()
    assert client.data_collection["http_headers"]["request"] == {"mode": "deny_list"}
    assert client.data_collection["http_headers"]["response"] == {"mode": "deny_list"}

    sentry_sdk.init(data_collection={"http_headers": "off"})  # type: ignore Purposely ignoring to test invalid option
    client = sentry_sdk.get_client()
    assert client.data_collection["http_headers"]["request"] == {"mode": "deny_list"}
    assert client.data_collection["http_headers"]["response"] == {"mode": "deny_list"}

    sentry_sdk.init()
    client = sentry_sdk.get_client()
    assert client.data_collection["http_headers"]["request"] == {"mode": "deny_list"}
    assert client.data_collection["http_headers"]["response"] == {"mode": "deny_list"}


def test_http_headers_use_default_in_setting_with_missing_config():
    data_collection_config: "DataCollectionUserOptions" = {
        "http_headers": {
            "request": {"mode": "allow_list", "terms": ["x-id"]},
        }
    }

    sentry_sdk.init(data_collection=data_collection_config)

    client = sentry_sdk.get_client()

    assert client.data_collection["http_headers"]["request"] == {
        "mode": "allow_list",
        "terms": ["x-id"],
    }
    assert client.data_collection["http_headers"]["response"] == {
        "mode": "deny_list",
    }


def test_http_headers_both_set():
    data_collection_config: "DataCollectionUserOptions" = {
        "http_headers": {
            "request": {"mode": "allow_list", "terms": ["x-id"]},
            "response": {"mode": "allow_list", "terms": ["foo"]},
        }
    }

    sentry_sdk.init(data_collection=data_collection_config)

    client = sentry_sdk.get_client()

    assert client.data_collection["http_headers"]["request"] == {
        "mode": "allow_list",
        "terms": ["x-id"],
    }
    assert client.data_collection["http_headers"]["response"] == {
        "mode": "allow_list",
        "terms": ["foo"],
    }


def _resolve_data_collection(**options):
    base = {
        "data_collection": None,
        "send_default_pii": None,
        "include_local_variables": True,
        "include_source_context": True,
    }
    base.update(options)
    return resolve_data_collection(base)


def _get(dc, path):
    obj = dc
    for part in path.split("."):
        obj = obj[part]
    return obj


@pytest.mark.parametrize(
    "options,expected",
    [
        pytest.param(
            {},
            {
                "user_info": False,
                "gen_ai.inputs": False,
                "gen_ai.outputs": False,
                "cookies.mode": "off",
                "query_params.mode": "off",
                "http_headers.request.mode": "deny_list",
                "http_bodies": ALL_HTTP_BODY_TYPES,
                "frame_context_lines": 5,
            },
            id="no_options_collects_no_pii",
        ),
        pytest.param(
            {"send_default_pii": True},
            {
                "user_info": True,
                "gen_ai.inputs": True,
                "gen_ai.outputs": True,
                "cookies.mode": "deny_list",
                "query_params.mode": "deny_list",
            },
            id="send_default_pii_true_collects_pii",
        ),
        pytest.param(
            {"send_default_pii": False},
            {"user_info": False, "cookies.mode": "off"},
            id="send_default_pii_false_collects_no_pii",
        ),
        pytest.param(
            {"data_collection": {}},
            {
                "user_info": True,
                "gen_ai.inputs": True,
                "gen_ai.outputs": True,
                "cookies.mode": "deny_list",
                "query_params.mode": "deny_list",
                "http_bodies": ALL_HTTP_BODY_TYPES,
            },
            id="explicit_data_collection_uses_spec_defaults",
        ),
        pytest.param(
            {"data_collection": {"user_info": False, "http_bodies": []}},
            {
                "user_info": False,
                "http_bodies": [],
                "gen_ai.inputs": True,
                "cookies.mode": "deny_list",
            },
            id="explicit_partial_fills_omitted_with_spec_defaults",
        ),
        pytest.param(
            {
                "data_collection": {},
                "include_local_variables": False,
                "include_source_context": False,
            },
            {"stack_frame_variables": False, "frame_context_lines": 0},
            id="explicit_frame_fields_fall_back_to_legacy_options",
        ),
        pytest.param(
            {
                "data_collection": {
                    "cookies": {"mode": "off"},
                    "query_params": {"mode": "allow_list", "terms": ["page"]},
                    "http_headers": {"request": {"mode": "off"}},
                    "gen_ai": {"inputs": False, "outputs": True},
                }
            },
            {
                "cookies.mode": "off",
                "query_params.mode": "allow_list",
                "query_params.terms": ["page"],
                "http_headers.request.mode": "off",
                "http_headers.response.mode": "deny_list",
                "gen_ai.inputs": False,
                "gen_ai.outputs": True,
            },
            id="accepts_dict_with_nested_dicts",
        ),
        pytest.param(
            {"data_collection": {}},
            {
                "graphql.document": True,
                "graphql.variables": True,
                "database.query_params": True,
            },
            id="explicit_graphql_database_defaults",
        ),
        pytest.param(
            {"send_default_pii": False},
            {
                "graphql.document": False,
                "graphql.variables": False,
                "database.query_params": False,
            },
            id="legacy_pii_off_gates_graphql_and_database",
        ),
        pytest.param(
            {"send_default_pii": True},
            {
                "graphql.document": True,
                "graphql.variables": True,
                "database.query_params": True,
            },
            id="legacy_pii_on_collects_graphql_and_database",
        ),
        pytest.param(
            {"data_collection": {"graphql": {"variables": False}}},
            {"graphql.document": True, "graphql.variables": False},
            id="explicit_partial_graphql_fills_omitted",
        ),
        pytest.param(
            {"data_collection": {"frame_context_lines": True}},
            {"frame_context_lines": 5},
            id="frame_context_lines_bool_fallback_true",
        ),
        pytest.param(
            {"data_collection": {"frame_context_lines": False}},
            {"frame_context_lines": 0},
            id="frame_context_lines_bool_fallback_false",
        ),
        pytest.param(
            {"data_collection": {"frame_context_lines": 3}},
            {"frame_context_lines": 3},
            id="frame_context_lines_bool_fallback_3",
        ),
        pytest.param(
            {"data_collection": {"frame_context_lines": 0}},
            {"frame_context_lines": 0},
            id="frame_context_lines_bool_fallback_0",
        ),
    ],
)
def test_resolve_data_collection(options, expected):
    dc = _resolve_data_collection(**options)
    for path, value in expected.items():
        assert _get(dc, path) == value, f"{path} != {value!r}"


def test_resolve_data_collection_overrides_send_default_pii_and_warns():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dc = _resolve_data_collection(
            send_default_pii=True, data_collection={"user_info": False}
        )
    assert dc["user_info"] is False  # data_collection wins
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@pytest.mark.parametrize(
    "init_kwargs,expected",
    [
        pytest.param(
            {},
            {
                "user_info": False,
                "provided_by_user": False,
                "should_send_default_pii": False,
            },
            id="defaults_to_no_pii",
        ),
        pytest.param(
            {"send_default_pii": True},
            {"user_info": True, "provided_by_user": False},
            id="send_default_pii_enables_user_info",
        ),
        pytest.param(
            {"data_collection": {"user_info": False}},
            {"user_info": False, "provided_by_user": True},
            id="explicit_data_collection_overrides_user_info",
        ),
        pytest.param(
            {"spotlight": True},
            {"provided_by_user": False, "user_info": True},
            id="dsnless_spotlight_rederives_data_collection",
        ),
        pytest.param(
            {"spotlight": True, "data_collection": {"user_info": False}},
            {"provided_by_user": True, "user_info": False},
            id="dsnless_spotlight_respects_explicit_data_collection",
        ),
    ],
)
def test_client_data_collection_settings(init_kwargs, expected):
    sentry_sdk.init(**init_kwargs)
    client = sentry_sdk.get_client()
    for key, value in expected.items():
        if key == "should_send_default_pii":
            assert client.should_send_default_pii() is value
        else:
            assert client.data_collection[key] is value
