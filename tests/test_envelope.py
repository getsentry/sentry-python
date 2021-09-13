from sentry_sdk.envelope import Envelope
from sentry_sdk.session import Session
from sentry_sdk import capture_event
from sentry_sdk.tracing_utils import compute_tracestate_value
import sentry_sdk.client

import pytest

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def generate_transaction_item():
    return {
        "event_id": "15210411201320122115110420122013",
        "type": "transaction",
        "transaction": "/interactions/other-dogs/new-dog",
        "start_timestamp": 1353568872.11122131,
        "timestamp": 1356942672.09040815,
        "contexts": {
            "trace": {
                "trace_id": "12312012123120121231201212312012",
                "span_id": "0415201309082013",
                "parent_span_id": None,
                "description": "<OrganizationContext>",
                "op": "greeting.sniff",
                "tracestate": compute_tracestate_value(
                    {
                        "trace_id": "12312012123120121231201212312012",
                        "environment": "dogpark",
                        "release": "off.leash.park",
                        "public_key": "dogsarebadatkeepingsecrets",
                        "user": {"id": 12312013, "segment": "bigs"},
                        "transaction": "/interactions/other-dogs/new-dog",
                    }
                ),
            }
        },
        "spans": [
            {
                "description": "<OrganizationContext>",
                "op": "greeting.sniff",
                "parent_span_id": None,
                "span_id": "0415201309082013",
                "start_timestamp": 1353568872.11122131,
                "timestamp": 1356942672.09040815,
                "trace_id": "12312012123120121231201212312012",
            }
        ],
    }


def test_add_and_get_basic_event():
    envelope = Envelope()

    expected = {"message": "Hello, World!"}
    envelope.add_event(expected)

    assert envelope.get_event() == {"message": "Hello, World!"}


def test_add_and_get_transaction_event():
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


def test_add_and_get_session():
    envelope = Envelope()

    expected = Session()
    envelope.add_session(expected)

    for item in envelope:
        if item.type == "session":
            assert item.payload.json == expected.to_json()


# TODO (kmclb) remove this parameterization once tracestate is a real feature
@pytest.mark.parametrize("tracestate_enabled", [True, False])
def test_envelope_headers(
    sentry_init, capture_envelopes, monkeypatch, tracestate_enabled
):
    monkeypatch.setattr(
        sentry_sdk.client,
        "format_timestamp",
        lambda x: "2012-11-21T12:31:12.415908Z",
    )

    monkeypatch.setattr(
        sentry_sdk.client,
        "has_tracestate_enabled",
        mock.Mock(return_value=tracestate_enabled),
    )

    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
    )
    envelopes = capture_envelopes()

    capture_event(generate_transaction_item())

    assert len(envelopes) == 1

    if tracestate_enabled:
        assert envelopes[0].headers == {
            "event_id": "15210411201320122115110420122013",
            "sent_at": "2012-11-21T12:31:12.415908Z",
            "trace": {
                "trace_id": "12312012123120121231201212312012",
                "environment": "dogpark",
                "release": "off.leash.park",
                "public_key": "dogsarebadatkeepingsecrets",
                "user": {"id": 12312013, "segment": "bigs"},
                "transaction": "/interactions/other-dogs/new-dog",
            },
        }
    else:
        assert envelopes[0].headers == {
            "event_id": "15210411201320122115110420122013",
            "sent_at": "2012-11-21T12:31:12.415908Z",
        }
