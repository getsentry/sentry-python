from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def init_serverless_sdk_module(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://foo@sentry.io/42")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")
    import scripts.init_serverless_sdk as init_sdk

    init_sdk._reset_loader()
    yield init_sdk
    init_sdk._reset_loader()


def test_handler_loaded_only_once(init_serverless_sdk_module, monkeypatch):
    init_sdk = init_serverless_sdk_module

    mock_handler = mock.MagicMock(return_value={"status": "ok"})

    monkeypatch.setenv("SENTRY_INITIAL_HANDLER", "fake_module.my_handler")

    with mock.patch(
        "scripts.init_serverless_sdk.AWSLambdaModuleLoader"
    ) as mock_loader_cls:
        instance = mock_loader_cls.return_value
        instance.get_lambda_handler.return_value = mock_handler

        # First invocation
        res1 = init_sdk.sentry_lambda_handler({"key": "val1"}, {"context": 1})
        assert res1 == {"status": "ok"}
        assert mock_loader_cls.call_count == 1
        assert instance.get_lambda_handler.call_count == 1
        assert mock_handler.call_count == 1
        mock_handler.assert_called_with({"key": "val1"}, {"context": 1})

        # Second invocation - should reuse cached handler without creating new AWSLambdaModuleLoader
        res2 = init_sdk.sentry_lambda_handler({"key": "val2"}, {"context": 2})
        assert res2 == {"status": "ok"}
        assert mock_loader_cls.call_count == 1
        assert instance.get_lambda_handler.call_count == 1
        assert mock_handler.call_count == 2
        mock_handler.assert_called_with({"key": "val2"}, {"context": 2})

        # Third invocation
        res3 = init_sdk.sentry_lambda_handler({"key": "val3"}, {"context": 3})
        assert res3 == {"status": "ok"}
        assert mock_loader_cls.call_count == 1
        assert mock_handler.call_count == 3


def test_handler_execution_with_real_loader(
    init_serverless_sdk_module, monkeypatch, tmp_path
):
    init_sdk = init_serverless_sdk_module

    # Create a real python file with a handler
    test_file = tmp_path / "my_lambda.py"
    test_file.write_text(
        "invocations = 0\n"
        "def handler(event, context):\n"
        "    global invocations\n"
        "    invocations += 1\n"
        "    return {'count': invocations, 'event': event}\n"
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SENTRY_INITIAL_HANDLER", "my_lambda.handler")

    original_extract = (
        init_sdk.AWSLambdaModuleLoader.extract_and_load_lambda_function_module
    )
    with mock.patch.object(
        init_sdk.AWSLambdaModuleLoader,
        "extract_and_load_lambda_function_module",
        side_effect=original_extract,
        autospec=True,
    ) as mock_extract:
        # First call
        res1 = init_sdk.sentry_lambda_handler({"call": 1}, None)
        assert res1["count"] == 1
        assert res1["event"] == {"call": 1}
        assert mock_extract.call_count == 1

        # Second call: handler module should NOT be re-extracted / reloaded
        res2 = init_sdk.sentry_lambda_handler({"call": 2}, None)
        assert res2["count"] == 2
        assert res2["event"] == {"call": 2}
        assert mock_extract.call_count == 1


def test_reset_loader_allows_reloading(init_serverless_sdk_module, monkeypatch):
    init_sdk = init_serverless_sdk_module

    mock_handler = mock.MagicMock(return_value="ok")
    monkeypatch.setenv("SENTRY_INITIAL_HANDLER", "fake_module.my_handler")

    with mock.patch(
        "scripts.init_serverless_sdk.AWSLambdaModuleLoader"
    ) as mock_loader_cls:
        mock_loader_cls.return_value.get_lambda_handler.return_value = mock_handler

        init_sdk.sentry_lambda_handler({}, {})
        assert mock_loader_cls.call_count == 1

        # Reset loader
        init_sdk._reset_loader()

        init_sdk.sentry_lambda_handler({}, {})
        assert mock_loader_cls.call_count == 2


def test_invalid_handler_path_raises_error(init_serverless_sdk_module, monkeypatch):
    init_sdk = init_serverless_sdk_module

    monkeypatch.setenv("SENTRY_INITIAL_HANDLER", "invalid_handler_no_dot")

    with pytest.raises(ValueError, match="Incorrect AWS Handler path"):
        init_sdk.sentry_lambda_handler({}, {})

    assert init_sdk._entry_point is None
    assert init_sdk._module_loader is None
