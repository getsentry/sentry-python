from sentry_sdk._compat import PY2

if PY2:
    from sentry_sdk.tracing_utils_py2 import start_child_span_decorator
else:
    from sentry_sdk.tracing_utils_py3 import start_child_span_decorator

from sentry_sdk.utils import logger

from tests.conftest import patch_start_tracing_child

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def my_example_function():
    return "return_of_sync_function"


def test_trace_decorator():
    with patch_start_tracing_child() as fake_start_child:
        result = my_example_function()
        fake_start_child.assert_not_called()
        assert result == "return_of_sync_function"

        result2 = start_child_span_decorator(my_example_function)()
        fake_start_child.assert_called_once_with(
            op="function", description="test_decorator_sync.my_example_function"
        )
        assert result2 == "return_of_sync_function"


def test_trace_decorator_no_trx():
    with patch_start_tracing_child(fake_transaction_is_none=True):
        with mock.patch.object(logger, "warning", mock.Mock()) as fake_warning:
            result = my_example_function()
            fake_warning.assert_not_called()
            assert result == "return_of_sync_function"

            result2 = start_child_span_decorator(my_example_function)()
            fake_warning.assert_called_once_with(
                "Can not create a child span for %s. "
                "Please start a Sentry transaction before calling this function.",
                "test_decorator_sync.my_example_function",
            )
            assert result2 == "return_of_sync_function"
