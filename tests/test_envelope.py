from sentry_sdk.envelope import Envelope
from sentry_sdk.session import Session
from sentry_sdk import capture_event
import sentry_sdk.client


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
                "dynamic_sampling_context": {
                    "trace_id": "12312012123120121231201212312012",
                    "sample_rate": "1.0",
                    "environment": "dogpark",
                    "release": "off.leash.park",
                    "public_key": "dogsarebadatkeepingsecrets",
                    "transaction": "/interactions/other-dogs/new-dog",
                },
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


def test_envelope_headers(sentry_init, capture_envelopes, monkeypatch):
    monkeypatch.setattr(
        sentry_sdk.client,
        "format_timestamp",
        lambda x: "2012-11-21T12:31:12.415908Z",
    )

    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

    capture_event(generate_transaction_item())

    assert len(envelopes) == 1

    assert envelopes[0].headers == {
        "event_id": "15210411201320122115110420122013",
        "sent_at": "2012-11-21T12:31:12.415908Z",
        "trace": {
            "trace_id": "12312012123120121231201212312012",
            "sample_rate": "1.0",
            "environment": "dogpark",
            "release": "off.leash.park",
            "public_key": "dogsarebadatkeepingsecrets",
            "transaction": "/interactions/other-dogs/new-dog",
        },
    }


def test_envelope_with_sized_items():
    """
    Tests that it successfully parses envelopes with
    the item size specified in the header
    """
    envelope_raw = (
        b'{"event_id":"9ec79c33ec9942ab8353589fcb2e04dc"}\n'
        b'{"type":"type1","length":4 }\n1234\n'
        b'{"type":"type2","length":4 }\nabcd\n'
        b'{"type":"type3","length":0}\n\n'
        b'{"type":"type4","length":4 }\nab12\n'
    )
    envelope_raw_eof_terminated = envelope_raw[:-1]

    for envelope in (envelope_raw, envelope_raw_eof_terminated):
        actual = Envelope.deserialize(envelope)

        items = [item for item in actual]

        assert len(items) == 4

        assert items[0].type == "type1"
        assert items[0].get_bytes() == b"1234"

        assert items[1].type == "type2"
        assert items[1].get_bytes() == b"abcd"

        assert items[2].type == "type3"
        assert items[2].get_bytes() == b""

        assert items[3].type == "type4"
        assert items[3].get_bytes() == b"ab12"

        assert actual.headers["event_id"] == "9ec79c33ec9942ab8353589fcb2e04dc"


def test_envelope_with_implicitly_sized_items():
    """
    Tests that it successfully parses envelopes with
    the item size not specified in the header
    """
    envelope_raw = (
        b'{"event_id":"9ec79c33ec9942ab8353589fcb2e04dc"}\n'
        b'{"type":"type1"}\n1234\n'
        b'{"type":"type2"}\nabcd\n'
        b'{"type":"type3"}\n\n'
        b'{"type":"type4"}\nab12\n'
    )
    envelope_raw_eof_terminated = envelope_raw[:-1]

    for envelope in (envelope_raw, envelope_raw_eof_terminated):
        actual = Envelope.deserialize(envelope)
        assert actual.headers["event_id"] == "9ec79c33ec9942ab8353589fcb2e04dc"

        items = [item for item in actual]

        assert len(items) == 4

        assert items[0].type == "type1"
        assert items[0].get_bytes() == b"1234"

        assert items[1].type == "type2"
        assert items[1].get_bytes() == b"abcd"

        assert items[2].type == "type3"
        assert items[2].get_bytes() == b""

        assert items[3].type == "type4"
        assert items[3].get_bytes() == b"ab12"


def test_envelope_with_two_attachments():
    """
    Test that items are correctly parsed in an envelope with to size specified items
    """
    two_attachments = (
        b'{"event_id":"9ec79c33ec9942ab8353589fcb2e04dc","dsn":"https://e12d836b15bb49d7bbf99e64295d995b:@sentry.io/42"}\n'
        + b'{"type":"attachment","length":10,"content_type":"text/plain","filename":"hello.txt"}\n'
        + b"\xef\xbb\xbfHello\r\n\n"
        + b'{"type":"event","length":41,"content_type":"application/json","filename":"application.log"}\n'
        + b'{"message":"hello world","level":"error"}\n'
    )
    two_attachments_eof_terminated = two_attachments[
        :-1
    ]  # last \n is optional, without it should still be a valid envelope

    for envelope_raw in (two_attachments, two_attachments_eof_terminated):
        actual = Envelope.deserialize(envelope_raw)
        items = [item for item in actual]

        assert len(items) == 2
        assert items[0].get_bytes() == b"\xef\xbb\xbfHello\r\n"
        assert items[1].payload.json == {"message": "hello world", "level": "error"}


def test_envelope_with_empty_attachments():
    """
    Test that items are correctly parsed in an envelope with two 0 length items (with size specified in the header
    """
    two_empty_attachments = (
        b'{"event_id":"9ec79c33ec9942ab8353589fcb2e04dc"}\n'
        + b'{"type":"attachment","length":0}\n\n'
        + b'{"type":"attachment","length":0}\n\n'
    )

    two_empty_attachments_eof_terminated = two_empty_attachments[
        :-1
    ]  # last \n is optional, without it should still be a valid envelope

    for envelope_raw in (two_empty_attachments, two_empty_attachments_eof_terminated):
        actual = Envelope.deserialize(envelope_raw)
        items = [item for item in actual]

        assert len(items) == 2
        assert items[0].get_bytes() == b""
        assert items[1].get_bytes() == b""


def test_envelope_without_headers():
    """
    Test that an envelope without headers is parsed successfully
    """
    envelope_without_headers = (
        b"{}\n" + b'{"type":"session"}\n' + b'{"started": "2020-02-07T14:16:00Z"}'
    )
    actual = Envelope.deserialize(envelope_without_headers)
    items = [item for item in actual]

    assert len(items) == 1
    assert items[0].payload.get_bytes() == b'{"started": "2020-02-07T14:16:00Z"}'
