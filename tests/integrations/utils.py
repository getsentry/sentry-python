import pytest

# Shared parametrization test matrix exercising the precedence between the legacy
# ``send_default_pii`` boolean and the ``data_collection.user_info`` setting.
# Each case is ``(init_kwargs, expect_user_info)`` where the second element indicates
# whether user info (IP address, user identity, etc.) is expected to be collected.
DATA_COLLECTION_USER_INFO_CASES = [
    pytest.param({"send_default_pii": True}, True, id="legacy_send_default_pii_true"),
    pytest.param(
        {"send_default_pii": False}, False, id="legacy_send_default_pii_false"
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": True}}},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"_experiments": {"data_collection": {"user_info": False}}},
        False,
        id="data_collection_user_info_false",
    ),
    pytest.param(
        {
            "send_default_pii": True,
            "_experiments": {"data_collection": {"user_info": False}},
        },
        False,
        id="data_collection_wins_over_send_default_pii_true",
    ),
    pytest.param(
        {
            "send_default_pii": False,
            "_experiments": {"data_collection": {"user_info": True}},
        },
        True,
        id="data_collection_wins_over_send_default_pii_false",
    ),
]
