import pytest
import sentry_sdk


@pytest.fixture
def capture_exceptions(monkeypatch):
    def inner():
        errors = set()
        old_capture_event_hub = sentry_sdk.Hub.capture_event
        old_capture_event_scope = sentry_sdk.Scope.capture_event

        def capture_event_hub(self, event, hint=None, scope=None):
            """
            Can be removed when we remove push_scope and the Hub from the SDK.
            """
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event_hub(self, event, hint=hint, scope=scope)

        def capture_event_scope(self, event, hint=None, scope=None):
            if hint:
                if "exc_info" in hint:
                    error = hint["exc_info"][1]
                    errors.add(error)
            return old_capture_event_scope(self, event, hint=hint, scope=scope)

        monkeypatch.setattr(sentry_sdk.Hub, "capture_event", capture_event_hub)
        monkeypatch.setattr(sentry_sdk.Scope, "capture_event", capture_event_scope)

        return errors

    return inner


parametrize_test_configurable_status_codes = pytest.mark.parametrize(
    ("failed_request_status_codes", "status_code", "expected_error"),
    (
        (None, 500, True),
        (None, 400, False),
        ({500, 501}, 500, True),
        ({500, 501}, 401, False),
        ({*range(400, 500)}, 401, True),
        ({*range(400, 500)}, 500, False),
        ({*range(400, 600)}, 300, False),
        ({*range(400, 600)}, 403, True),
        ({*range(400, 600)}, 503, True),
        ({*range(400, 403), 500, 501}, 401, True),
        ({*range(400, 403), 500, 501}, 405, False),
        ({*range(400, 403), 500, 501}, 501, True),
        ({*range(400, 403), 500, 501}, 503, False),
        (set(), 500, False),
    ),
)
