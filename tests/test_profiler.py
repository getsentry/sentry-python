import inspect

import pytest

from sentry_sdk.profiler import extract_stack, get_frame_name


def get_frame(depth=1):
    """
    This function is not exactly true to its name. Depending on
    how it is called, the true depth of the stack can be deeper
    than the argument implies.
    """
    if depth <= 0:
        raise ValueError("only positive integers allowed")
    if depth > 1:
        return get_frame(depth=depth - 1)
    return inspect.currentframe()


class GetFrame:
    def instance_method(self):
        return inspect.currentframe()

    @classmethod
    def class_method(cls):
        return inspect.currentframe()

    @staticmethod
    def static_method():
        return inspect.currentframe()


@pytest.mark.parametrize(
    ("frame", "frame_name"),
    [
        pytest.param(
            get_frame(),
            "get_frame",
            id="function",
        ),
        pytest.param(
            (lambda: inspect.currentframe())(),
            "<lambda>",
            id="lambda",
        ),
        pytest.param(
            GetFrame().instance_method(),
            "GetFrame.instance_method",
            id="instance_method",
        ),
        pytest.param(
            GetFrame().class_method(),
            "GetFrame.class_method",
            id="class_method",
        ),
        pytest.param(
            GetFrame().static_method(),
            "GetFrame.static_method",
            id="static_method",
            marks=pytest.mark.skip(reason="unsupported"),
        ),
    ],
)
def test_get_frame_name(frame, frame_name):
    assert get_frame_name(frame) == frame_name


@pytest.mark.parametrize(
    ("depth", "max_stack_depth", "actual_depth"),
    [
        pytest.param(1, 128, 1, id="less than"),
        pytest.param(256, 128, 128, id="greater than"),
        pytest.param(128, 128, 128, id="equals"),
    ],
)
def test_extract_stack_with_max_depth(depth, max_stack_depth, actual_depth):
    # introduce a lambda that we'll be looking for in the stack
    frame = (lambda: get_frame(depth=depth))()

    # plus 1 because we introduced a lambda intentionally that we'll
    # look for in the final stack to make sure its in the right position
    base_stack_depth = len(inspect.stack()) + 1

    # increase the max_depth by the `base_stack_depth` to account
    # for the extra frames pytest will add
    stack = extract_stack(frame, max_stack_depth + base_stack_depth)
    assert len(stack) == base_stack_depth + actual_depth

    for i in range(actual_depth):
        assert stack[i].name == "get_frame", i

    # index 0 contains the inner most frame on the stack, so the lamdba
    # should be at index `actual_depth`
    assert stack[actual_depth].name == "<lambda>", actual_depth
