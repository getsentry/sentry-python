import sentry_sdk
from sentry_sdk.integrations.modules import ModulesIntegration, _InstalledModules
from sentry_sdk.serializer import _Serializer, serialize
from sentry_sdk.utils import _get_installed_modules


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())

    (event,) = events
    assert event["modules"] == _get_installed_modules()


def test_before_send_modules_mutations_do_not_leak(sentry_init, capture_events):
    modules_seen = []

    def before_send(event, hint):
        assert type(event["modules"]) is dict
        modules_seen.append(event["modules"].copy())
        event["modules"]["sentry-sdk"] = "mutated"
        return event

    sentry_init(integrations=[ModulesIntegration()], before_send=before_send)
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())
    sentry_sdk.capture_exception(ValueError())

    assert len(events) == 2
    assert modules_seen == [_get_installed_modules(), _get_installed_modules()]


def test_installed_modules_are_not_recursively_serialized(monkeypatch):
    original_serialize_node = _Serializer._serialize_node
    segments = []

    def counting_serialize_node(self, obj, *args, **kwargs):
        segments.append(kwargs.get("segment"))
        return original_serialize_node(self, obj, *args, **kwargs)

    monkeypatch.setattr(_Serializer, "_serialize_node", counting_serialize_node)

    event = serialize({"modules": _InstalledModules({"package": "1.0"})})

    assert event["modules"] == {"package": "1.0"}
    assert segments.count("modules") == 1
    assert "package" not in segments
