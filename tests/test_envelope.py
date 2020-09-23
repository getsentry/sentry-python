from sentry_sdk.envelope import Envelope
from sentry_sdk.sessions import Session


def generate_transaction_item():
    return {
        "event_id": "d2132d31b39445f1938d7e21b6bf0ec4",
        "type": "transaction",
        "transaction": "/organizations/:orgId/performance/:eventSlug/",
        "start_timestamp": 1597976392.6542819,
        "timestamp": 1597976400.6189718,
        "contexts": {
            "trace": {
                "trace_id": "4C79F60C11214EB38604F4AE0781BFB2",
                "span_id": "FA90FDEAD5F74052",
                "type": "trace",
            }
        },
        "spans": [
            {
                "description": "<OrganizationContext>",
                "op": "react.mount",
                "parent_span_id": "8f5a2b8768cafb4e",
                "span_id": "bd429c44b67a3eb4",
                "start_timestamp": 1597976393.4619668,
                "timestamp": 1597976393.4718769,
                "trace_id": "ff62a8b040f340bda5d830223def1d81",
            }
        ],
    }


def test_basic_event():
    envelope = Envelope()

    expected = {"message": "Hello, World!"}
    envelope.add_event(expected)

    assert envelope.get_event() == {"message": "Hello, World!"}


def test_transaction_event():
    envelope = Envelope()

    transaction_item = generate_transaction_item()
    transaction_item.update({"event_id": "a" * 32})
    envelope.add_transaction(transaction_item)

    # typically it should not be possible to be able to add a second transaction;
    # but we do it anyways
    another_transaction_item = generate_transaction_item()
    envelope.add_transaction(another_transaction_item)

    # should only fetch the first inserted transaction event
    assert envelope.get_transaction_event() == transaction_item


def test_session():
    envelope = Envelope()

    expected = Session()
    envelope.add_session(expected)

    for item in envelope:
        if item.type == "session":
            assert item.payload.json == expected.to_json()
