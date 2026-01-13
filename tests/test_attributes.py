import sentry_sdk

from tests.test_metrics import envelopes_to_metrics


def test_scope_precedence(sentry_init, capture_envelopes):
    # Order of precedence, from most important to least:
    # 1. telemetry attributes (directly supplying attributes on creation or using set_attribute)
    # 2. current scope attributes
    # 3. isolation scope attributes
    # 4. global scope attributes
    sentry_init()

    envelopes = capture_envelopes()

    global_scope = sentry_sdk.get_global_scope()
    global_scope.set_attribute("global.attribute", "global")
    global_scope.set_attribute("overwritten.attribute", "global")

    isolation_scope = sentry_sdk.get_isolation_scope()
    isolation_scope.set_attribute("isolation.attribute", "isolation")
    isolation_scope.set_attribute("overwritten.attribute", "isolation")

    current_scope = sentry_sdk.get_current_scope()
    current_scope.set_attribute("current.attribute", "current")
    current_scope.set_attribute("overwritten.attribute", "current")

    sentry_sdk.metrics.count("test", 1)
    sentry_sdk.get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    (metric,) = metrics

    assert metric["attributes"]["global.attribute"] == "global"
    assert metric["attributes"]["isolation.attribute"] == "isolation"
    assert metric["attributes"]["current.attribute"] == "current"

    assert metric["attributes"]["overwritten.attribute"] == "current"


def test_telemetry_precedence(sentry_init, capture_envelopes):
    # Order of precedence, from most important to least:
    # 1. telemetry attributes (directly supplying attributes on creation or using set_attribute)
    # 2. current scope attributes
    # 3. isolation scope attributes
    # 4. global scope attributes
    sentry_init()

    envelopes = capture_envelopes()

    global_scope = sentry_sdk.get_global_scope()
    global_scope.set_attribute("global.attribute", "global")
    global_scope.set_attribute("overwritten.attribute", "global")

    isolation_scope = sentry_sdk.get_isolation_scope()
    isolation_scope.set_attribute("isolation.attribute", "isolation")
    isolation_scope.set_attribute("overwritten.attribute", "isolation")

    current_scope = sentry_sdk.get_current_scope()
    current_scope.set_attribute("current.attribute", "current")
    current_scope.set_attribute("overwritten.attribute", "current")

    sentry_sdk.metrics.count(
        "test",
        1,
        attributes={
            "telemetry.attribute": "telemetry",
            "overwritten.attribute": "telemetry",
        },
    )

    sentry_sdk.get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    (metric,) = metrics

    assert metric["attributes"]["global.attribute"] == "global"
    assert metric["attributes"]["isolation.attribute"] == "isolation"
    assert metric["attributes"]["current.attribute"] == "current"
    assert metric["attributes"]["telemetry.attribute"] == "telemetry"

    assert metric["attributes"]["overwritten.attribute"] == "telemetry"


def test_attribute_out_of_scope(sentry_init, capture_envelopes):
    sentry_init()

    envelopes = capture_envelopes()

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("outofscope.attribute", "out of scope")

    sentry_sdk.metrics.count("test", 1)

    sentry_sdk.get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    (metric,) = metrics

    assert "outofscope.attribute" not in metric["attributes"]


def test_remove_attribute(sentry_init, capture_envelopes):
    sentry_init()

    envelopes = capture_envelopes()

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("some.attribute", 123)
        scope.remove_attribute("some.attribute")

        sentry_sdk.metrics.count("test", 1)

    sentry_sdk.get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    (metric,) = metrics

    assert "some.attribute" not in metric["attributes"]


def test_scope_attributes_preserialized(sentry_init, capture_envelopes):
    def before_send_metric(metric, _):
        # Scope attrs show up serialized in before_send
        assert isinstance(metric["attributes"]["instance"], str)
        assert isinstance(metric["attributes"]["dictionary"], str)

        return metric

    sentry_init(before_send_metric=before_send_metric)

    envelopes = capture_envelopes()

    class Cat:
        pass

    instance = Cat()
    dictionary = {"color": "tortoiseshell"}

    with sentry_sdk.new_scope() as scope:
        scope.set_attribute("instance", instance)
        scope.set_attribute("dictionary", dictionary)

        # Scope attrs are stored preserialized
        assert isinstance(scope._attributes["instance"], str)
        assert isinstance(scope._attributes["dictionary"], str)

        sentry_sdk.metrics.count("test", 1)

    sentry_sdk.get_client().flush()

    metrics = envelopes_to_metrics(envelopes)
    (metric,) = metrics

    # Attrs originally from the scope are serialized when sent
    assert isinstance(metric["attributes"]["instance"], str)
    assert isinstance(metric["attributes"]["dictionary"], str)
