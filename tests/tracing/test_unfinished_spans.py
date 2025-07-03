import sentry_sdk


def perform_test(sentry_init, capture_events):
    sentry_init(
        traces_sample_rate=1.0,
    )

    events = capture_events()

    with sentry_sdk.start_transaction(name="test"):
        with sentry_sdk.start_span(name="span0"):
            pass

        span1 = sentry_sdk.start_span(name="span1").__enter__()

        with sentry_sdk.start_span(name="span2"):
            pass

    span1.__exit__(None, None, None)

    return events, span1


def test_unfinished_spans_sdk_2(sentry_init, capture_events):
    events, span1 = perform_test(sentry_init, capture_events)

    (event,) = events

    assert event["transaction"] == "test"

    spans_by_name = {span["description"]: span for span in event["spans"]}

    assert {"span0", "span2"} == spans_by_name.keys()
    assert (
        spans_by_name["span0"]["parent_span_id"]
        == event["contexts"]["trace"]["span_id"]
    )
    assert spans_by_name["span2"]["parent_span_id"] == span1.span_id


def test_unfinished_spans_sdk_3(sentry_init, capture_events):
    events, _ = perform_test(sentry_init, capture_events)

    (event,) = events

    assert event["transaction"] == "test"

    spans_by_name = {span["description"]: span for span in event["spans"]}

    assert {"span0"} == spans_by_name.keys()
    assert (
        spans_by_name["span0"]["parent_span_id"]
        == event["contexts"]["trace"]["span_id"]
    )
