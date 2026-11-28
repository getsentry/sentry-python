import sentry_sdk
from sentry_sdk.integrations.modules import ModulesIntegration


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    sentry_sdk.capture_exception(ValueError())

    (event,) = events
    assert "sentry-sdk" in event["modules"]
    assert "pytest" in event["modules"]


def test_modules_not_reserialized_per_event(sentry_init, capture_events, monkeypatch):
    from sentry_sdk.serializer import _Serializer

    fake_modules = {f"package-{i}": f"{i}.0.0" for i in range(50)}
    monkeypatch.setattr(
        "sentry_sdk.integrations.modules._get_installed_modules",
        lambda: fake_modules,
    )

    sentry_init(integrations=[ModulesIntegration()])
    events = capture_events()

    module_node_calls = 0
    orig_serialize_node = _Serializer._serialize_node

    def counting_serialize_node(self, obj, *args, **kwargs):
        nonlocal module_node_calls
        if self.path == ["modules"]:
            module_node_calls += 1
        return orig_serialize_node(self, obj, *args, **kwargs)

    monkeypatch.setattr(_Serializer, "_serialize_node", counting_serialize_node)

    for _ in range(3):
        sentry_sdk.capture_message("hello")

    assert len(events) == 3
    for event in events:
        assert type(event["modules"]) is dict
        assert event["modules"] == fake_modules

    # Modules should be serialized at most once when populating the cache.
    assert module_node_calls <= len(fake_modules)


def test_modules_mutation_isolation(sentry_init, capture_events, monkeypatch):
    fake_modules = {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}
    monkeypatch.setattr(
        "sentry_sdk.integrations.modules._get_installed_modules",
        lambda: fake_modules,
    )

    call_count = 0

    def before_send(event, hint):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            event["modules"]["injected"] = "9.9.9"
            del event["modules"]["pkg-a"]
        return event

    sentry_init(integrations=[ModulesIntegration()], before_send=before_send)
    events = capture_events()

    sentry_sdk.capture_message("first")
    sentry_sdk.capture_message("second")

    assert len(events) == 2
    assert events[0]["modules"] == {"pkg-b": "2.0.0", "injected": "9.9.9"}
    assert events[1]["modules"] == {"pkg-a": "1.0.0", "pkg-b": "2.0.0"}


def test_modules_event_processor_mutation(sentry_init, capture_events, monkeypatch):
    fake_modules = {"pkg-a": "1.0.0"}
    monkeypatch.setattr(
        "sentry_sdk.integrations.modules._get_installed_modules",
        lambda: fake_modules,
    )

    sentry_init(integrations=[ModulesIntegration()], max_value_length=10)
    events = capture_events()

    class CustomObj:
        def __repr__(self):
            return "<custom>"

    scope = sentry_sdk.get_isolation_scope()

    @scope.add_event_processor
    def mutate_modules(event, hint):
        if event.get("message") == "mutate":
            event["modules"]["extra"] = CustomObj()
            event["modules"]["long-pkg"] = "123456789012345"
        return event

    sentry_sdk.capture_message("mutate")
    sentry_sdk.capture_message("clean")

    assert len(events) == 2
    assert events[0]["modules"] == {
        "pkg-a": "1.0.0",
        "extra": "<custom>",
        "long-pkg": "1234567...",
    }
    assert "_meta" not in events[0]["modules"]
    assert events[1]["modules"] == {"pkg-a": "1.0.0"}


def test_modules_respects_max_value_length(sentry_init, capture_events, monkeypatch):
    fake_modules = {"pkg-long": "1234567890"}
    monkeypatch.setattr(
        "sentry_sdk.integrations.modules._get_installed_modules",
        lambda: fake_modules,
    )

    sentry_init(integrations=[ModulesIntegration()], max_value_length=5)
    events = capture_events()

    sentry_sdk.capture_message("hello")
    (event,) = events
    assert event["modules"] == {"pkg-long": "12..."}
