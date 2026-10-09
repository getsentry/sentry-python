import pytest


def test_kvcb_invalid_mode(sentry_init):
    with pytest.raises(ValueError):
        sentry_init(data_collection={"cookies": {"mode": "nope"}})  # type: ignore Purposely ignoring to test invalid option


@pytest.mark.parametrize(
    "value",
    ["3", -1, [1], 2.5],
    ids=[
        "frame_context_lines_string",
        "frame_context_lines_negative",
        "frame_context_lines_list",
        "frame_context_lines_float",
    ],
)
def test_frame_context_lines_invalid_value(sentry_init, value):
    with pytest.raises(ValueError):
        sentry_init(data_collection={"frame_context_lines": value})
