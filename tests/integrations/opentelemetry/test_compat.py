import sentry_sdk


def test_transaction_name_span_description_compat(
    sentry_init,
    capture_events,
):
    sentry_init(traces_sample_rate=1.0)

    events = capture_events()

    with sentry_sdk.start_span(
        name="trx-name",
        op="trx-op",
    ) as trx:
        with sentry_sdk.start_span(
            description="span-desc",
            op="span-op",
        ) as spn:
            ...

    assert trx.__class__.__name__ == "Span"
    assert trx.op == "trx-op"
    assert trx.name == "trx-name"
    assert trx.description is None

    assert trx._otel_span is not None
    assert trx._otel_span.name == "trx-name"
    assert trx._otel_span.attributes["sentry.op"] == "trx-op"
    assert trx._otel_span.attributes["sentry.name"] == "trx-name"
    assert "sentry.description" not in trx._otel_span.attributes

    assert spn.__class__.__name__ == "Span"
    assert spn.op == "span-op"
    assert spn.description == "span-desc"
    assert spn.name == "span-desc"

    assert spn._otel_span is not None
    assert spn._otel_span.name == "span-desc"
    assert spn._otel_span.attributes["sentry.op"] == "span-op"
    assert spn._otel_span.attributes["sentry.description"] == "span-desc"

    transaction = events[0]
    assert transaction["transaction"] == "trx-name"
    assert transaction["contexts"]["trace"]["op"] == "trx-op"
    assert transaction["contexts"]["trace"]["data"]["sentry.op"] == "trx-op"
    assert transaction["contexts"]["trace"]["data"]["sentry.name"] == "trx-name"
    assert "sentry.description" not in transaction["contexts"]["trace"]["data"]

    span = transaction["spans"][0]
    assert span["description"] == "span-desc"
    assert span["op"] == "span-op"
    assert span["data"]["sentry.op"] == "span-op"
    assert span["data"]["sentry.description"] == "span-desc"
