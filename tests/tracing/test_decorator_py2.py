from sentry_sdk.tracing_utils_py2 import (
    start_child_span_decorator as start_child_span_decorator_py2,
)
from sentry_sdk.utils import logger

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def my_example_function():
    return "return_of_sync_function"


def test_trace_decorator_py2():
    fake_start_child = mock.MagicMock()
    fake_transaction = mock.MagicMock()
    fake_transaction.start_child = fake_start_child

    with mock.patch(
        "sentry_sdk.tracing_utils_py2.get_current_span",
        return_value=fake_transaction,
    ):
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        result2 = start_child_span_decorator_py2(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", description="test_decorator_py2.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_py2_no_trx():
    fake_transaction = None

    with mock.patch(
        "sentry_sdk.tracing_utils_py2.get_current_span",
        return_value=fake_transaction,
    ):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = my_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_sync_function"

            result2 = start_child_span_decorator_py2(my_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator_py2.my_example_function",
            )
            assert result2 == "return_of_sync_function"
