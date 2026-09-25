import pytest

# Shared parametrization test matrix for ``data_collection.user_info`` setting.
# Each case is ``(data_collection, expect_user_info)`` where the second element indicates
# whether user info (IP address, user identity, etc.) is expected to be collected.
DATA_COLLECTION_USER_INFO_CASES = [
    pytest.param(
        {"user_info": True},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"user_info": False},
        False,
        id="data_collection_user_info_false",
    ),
]

# Shared parametrization test matrix for ``REMOTE_ADDR`` on events.
# Each case is ``(data_collection, expect_remote_addr)``.
DATA_COLLECTION_REMOTE_ADDR_CASES = [
    pytest.param({}, True, id="data_collection_default"),
    pytest.param(
        {"user_info": True},
        True,
        id="data_collection_user_info_true",
    ),
    pytest.param(
        {"user_info": False},
        False,
        id="data_collection_user_info_false",
    ),
]


# Shared parametrization test matrix for ``data_collection.queues`` setting.
# Each case is ``(data_collection, expected_args, expected_kwargs)`` where ``None`` for
# the expected values means args/kwargs are not collected at all.
DATA_COLLECTION_QUEUES_CASES = [
    pytest.param(
        {},
        [1],
        {"b": 0},
        id="data_collection_default",
    ),
    pytest.param(
        {"queues": True},
        [1],
        {"b": 0},
        id="data_collection_queues_on",
    ),
    pytest.param(
        {"queues": False},
        None,
        None,
        id="data_collection_queues_off",
    ),
]


DATA_COLLECTION_USER_INFO_CASES_LEGACY = [
    pytest.param({"data_collection": case.values[0]}, case.values[1], id=case.id)
    for case in DATA_COLLECTION_USER_INFO_CASES
]
DATA_COLLECTION_REMOTE_ADDR_CASES_LEGACY = [
    pytest.param({"data_collection": case.values[0]}, case.values[1], id=case.id)
    for case in DATA_COLLECTION_REMOTE_ADDR_CASES
]
DATA_COLLECTION_QUEUES_CASES_LEGACY = [
    pytest.param(
        {"data_collection": case.values[0]},
        case.values[1],
        case.values[2],
        id=case.id,
    )
    for case in DATA_COLLECTION_QUEUES_CASES
]
