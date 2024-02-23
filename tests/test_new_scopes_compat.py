import sentry_sdk
from sentry_sdk.hub import Hub

"""
Those tests are meant to check the compatibility of the new scopes in SDK 2.0 with the old Hub/Scope system in SDK 1.x.

Those tests have been run with the latest SDK 1.x versiona and the data used in the `assert` statements represents
the behvaior of the SDK 1.x.

This makes sure that we are backwards compatible. (on a best effort basis, there will probably be some edge cases that are not covered here)
"""


def test_configure_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with configure_scope` block.

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with sentry_sdk.configure_scope() as scope:  # configure scope
        sentry_sdk.set_tag("B1", 1)
        scope.set_tag("B2", 1)
        sentry_sdk.capture_message("Event B")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1}
    assert event_z["tags"] == {"A": 1, "B1": 1, "B2": 1, "Z": 1}


def test_push_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with push_scope` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with sentry_sdk.push_scope() as scope:  # push scope
        sentry_sdk.set_tag("B1", 1)
        scope.set_tag("B2", 1)
        sentry_sdk.capture_message("Event B")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1}
    assert event_z["tags"] == {"A": 1, "Z": 1}


def test_with_hub_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with Hub:` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub.current as hub:  # with hub
        sentry_sdk.set_tag("B1", 1)
        hub.scope.set_tag("B2", 1)
        sentry_sdk.capture_message("Event B")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1}
    assert event_z["tags"] == {"A": 1, "B1": 1, "B2": 1, "Z": 1}


def test_with_hub_configure_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with Hub:` containing a `with configure_scope` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub.current as hub:  # with hub
        sentry_sdk.set_tag("B1", 1)
        with hub.configure_scope() as scope:  # configure scope
            sentry_sdk.set_tag("B2", 1)
            hub.scope.set_tag("B3", 1)
            scope.set_tag("B4", 1)
            sentry_sdk.capture_message("Event B")
        sentry_sdk.set_tag("B5", 1)
        sentry_sdk.capture_message("Event C")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_c, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1}
    assert event_c["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1, "B5": 1}
    assert event_z["tags"] == {
        "A": 1,
        "B1": 1,
        "B2": 1,
        "B3": 1,
        "B4": 1,
        "B5": 1,
        "Z": 1,
    }


def test_with_hub_push_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with Hub:` containing a `with push_scope` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub.current as hub:  # with hub
        sentry_sdk.set_tag("B1", 1)
        with hub.push_scope() as scope:  # push scope
            sentry_sdk.set_tag("B2", 1)
            hub.scope.set_tag("B3", 1)
            scope.set_tag("B4", 1)
            sentry_sdk.capture_message("Event B")
        sentry_sdk.set_tag("B5", 1)
        sentry_sdk.capture_message("Event C")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_c, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1}
    assert event_c["tags"] == {"A": 1, "B1": 1, "B5": 1}
    assert event_z["tags"] == {"A": 1, "B1": 1, "B5": 1, "Z": 1}


def test_with_cloned_hub_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with cloned Hub:` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub(Hub.current) as hub:  # clone hub
        sentry_sdk.set_tag("B1", 1)
        hub.scope.set_tag("B2", 1)
        sentry_sdk.capture_message("Event B")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1}
    assert event_z["tags"] == {"A": 1, "Z": 1}


def test_with_cloned_hub_configure_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with cloned Hub:` containing a `with configure_scope` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub(Hub.current) as hub:  # clone hub
        sentry_sdk.set_tag("B1", 1)
        with hub.configure_scope() as scope:  # configure scope
            sentry_sdk.set_tag("B2", 1)
            hub.scope.set_tag("B3", 1)
            scope.set_tag("B4", 1)
            sentry_sdk.capture_message("Event B")
        sentry_sdk.set_tag("B5", 1)
        sentry_sdk.capture_message("Event C")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_c, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1}
    assert event_c["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1, "B5": 1}
    assert event_z["tags"] == {"A": 1, "Z": 1}


def test_with_cloned_hub_push_scope_sdk1(sentry_init, capture_events):
    """
    Mutate data in a `with cloned Hub:` containing a `with push_scope` block

    Checks the results of SDK 2.x against the results the same code returned in SDK 1.x.
    """
    sentry_init()

    events = capture_events()

    sentry_sdk.set_tag("A", 1)
    sentry_sdk.capture_message("Event A")

    with Hub(Hub.current) as hub:  # clone hub
        sentry_sdk.set_tag("B1", 1)
        with hub.push_scope() as scope:  # push scope
            sentry_sdk.set_tag("B2", 1)
            hub.scope.set_tag("B3", 1)
            scope.set_tag("B4", 1)
            sentry_sdk.capture_message("Event B")
        sentry_sdk.set_tag("B5", 1)
        sentry_sdk.capture_message("Event C")

    sentry_sdk.set_tag("Z", 1)
    sentry_sdk.capture_message("Event Z")

    (event_a, event_b, event_c, event_z) = events

    # Check against the results the same code returned in SDK 1.x
    assert event_a["tags"] == {"A": 1}
    assert event_b["tags"] == {"A": 1, "B1": 1, "B2": 1, "B3": 1, "B4": 1}
    assert event_c["tags"] == {"A": 1, "B1": 1, "B5": 1}
    assert event_z["tags"] == {"A": 1, "Z": 1}
