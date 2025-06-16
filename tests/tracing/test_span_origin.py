import pytest
from sentry_sdk import start_span


def test_span_origin_manual(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="foo", name="bar"):
            pass

    (event,) = events

    assert len(events) == 1
    assert event["spans"][0]["origin"] == "manual"
    assert event["contexts"]["trace"]["origin"] == "manual"


def test_span_origin_custom(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0)
    events = capture_events()

    with start_span(name="hi"):
        with start_span(op="foo", name="bar", origin="foo.foo2.foo3"):
            pass

    with start_span(name="ho", origin="ho.ho2.ho3"):
        with start_span(op="baz", name="qux", origin="baz.baz2.baz3"):
            pass

    (first_transaction, second_transaction) = events

    assert len(events) == 2
    assert first_transaction["contexts"]["trace"]["origin"] == "manual"
    assert first_transaction["spans"][0]["origin"] == "foo.foo2.foo3"

    assert second_transaction["contexts"]["trace"]["origin"] == "ho.ho2.ho3"
    assert second_transaction["spans"][0]["origin"] == "baz.baz2.baz3"


@pytest.mark.parametrize("excluded_origins", [None, [], "noop"])
def test_exclude_span_origins_empty(sentry_init, capture_events, excluded_origins):
    if excluded_origins in (None, []):
        sentry_init(traces_sample_rate=1.0, exclude_span_origins=excluded_origins)
    elif excluded_origins == "noop":
        sentry_init(
            traces_sample_rate=1.0,
            # default is None
        )

    events = capture_events()

    with start_span(name="span1"):
        pass
    with start_span(name="span2", origin="auto.http.requests"):
        pass
    with start_span(name="span3", origin="auto.db.postgres"):
        pass

    assert len(events) == 3


@pytest.mark.parametrize(
    "excluded_origins,origins,expected_events_count,expected_allowed_origins",
    [
        # Regexes
        (
            [r"auto\.http\..*", r"auto\.db\..*"],
            [
                "auto.http.requests",
                "auto.db.sqlite",
                "manual",
            ],
            1,
            ["manual"],
        ),
        # Substring matching
        (
            ["http"],
            [
                "auto.http.requests",
                "http.client",
                "my.http.integration",
                "manual",
                "auto.db.postgres",
            ],
            2,
            ["manual", "auto.db.postgres"],
        ),
        # Mix and match
        (
            ["manual", r"auto\.http\..*", "db"],
            [
                "manual",
                "auto.http.requests",
                "auto.db.postgres",
                "auto.grpc.server",
            ],
            1,
            ["auto.grpc.server"],
        ),
    ],
)
def test_exclude_span_origins_patterns(
    sentry_init,
    capture_events,
    excluded_origins,
    origins,
    expected_events_count,
    expected_allowed_origins,
):
    sentry_init(
        traces_sample_rate=1.0,
        exclude_span_origins=excluded_origins,
    )

    events = capture_events()

    for origin in origins:
        with start_span(name="span", origin=origin):
            pass

    # Check total events captured
    assert len(events) == expected_events_count

    # Check that only expected origins were captured
    if expected_events_count > 0:
        captured_origins = {event["contexts"]["trace"]["origin"] for event in events}
        assert captured_origins == set(expected_allowed_origins)


def test_exclude_span_origins_with_child_spans(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, exclude_span_origins=[r"auto\.http\..*"])
    events = capture_events()

    with start_span(name="parent", origin="manual"):
        with start_span(name="http-child", origin="auto.http.requests"):
            pass
        with start_span(name="db-child", origin="auto.db.postgres"):
            pass

    assert len(events) == 1
    assert events[0]["contexts"]["trace"]["origin"] == "manual"
    assert len(events[0]["spans"]) == 1
    assert events[0]["spans"][0]["origin"] == "auto.db.postgres"


def test_exclude_span_origins_parent_with_child_spans(sentry_init, capture_events):
    sentry_init(traces_sample_rate=1.0, exclude_span_origins=[r"auto\.http\..*"])
    events = capture_events()

    with start_span(name="parent", origin="auto.http.requests"):
        with start_span(
            name="db-child", origin="auto.db.postgres", only_if_parent=True
        ):
            # Note: without only_if_parent, the child span would be promoted to a transaction
            pass

    assert len(events) == 0
