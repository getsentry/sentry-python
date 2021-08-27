import json

import pytest

import sentry_sdk
from sentry_sdk.tracing import Transaction, Span
from sentry_sdk.tracing_utils import (
    compute_tracestate_value,
    extract_sentrytrace_data,
    extract_tracestate_data,
    reinflate_tracestate,
)
from sentry_sdk.utils import from_base64, to_base64


try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_tracestate_computation(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
    )

    # force lazy computation to create a value
    transaction.to_tracestate()

    computed_value = transaction._sentry_tracestate.replace("sentry=", "")
    # we have to decode and reinflate the data because we can guarantee that the
    # order of the entries in the jsonified dict will be the same here as when
    # the tracestate is computed
    reinflated_trace_data = json.loads(from_base64(computed_value))

    assert reinflated_trace_data == {
        "trace_id": "12312012123120121231201212312012",
        "environment": "dogpark",
        "release": "off.leash.park",
        "public_key": "dogsarebadatkeepingsecrets",
        "transaction": "/interactions/other-dogs/new-dog",
    }


def test_doesnt_add_new_tracestate_to_transaction_when_none_given(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        # sentry_tracestate=< value would be passed here >
    )

    assert transaction._sentry_tracestate is None


def test_adds_tracestate_to_transaction_when_to_traceparent_called(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    )

    # no inherited tracestate, and none created in Transaction constructor
    assert transaction._sentry_tracestate is None

    transaction.to_tracestate()

    assert transaction._sentry_tracestate is not None


def test_adds_tracestate_to_transaction_when_getting_trace_context(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    )

    # no inherited tracestate, and none created in Transaction constructor
    assert transaction._sentry_tracestate is None

    transaction.get_trace_context()

    assert transaction._sentry_tracestate is not None


@pytest.mark.parametrize(
    "set_by", ["inheritance", "to_tracestate", "get_trace_context"]
)
def test_tracestate_is_immutable_once_set(sentry_init, monkeypatch, set_by):
    monkeypatch.setattr(
        sentry_sdk.tracing,
        "compute_tracestate_entry",
        mock.Mock(return_value="sentry=doGsaREgReaT"),
    )

    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    # for each scenario, get to the point where tracestate has been set
    if set_by == "inheritance":
        transaction = Transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
            sentry_tracestate=("sentry=doGsaREgReaT"),
        )
    else:
        transaction = Transaction(
            name="/interactions/other-dogs/new-dog",
            op="greeting.sniff",
        )

        if set_by == "to_tracestate":
            transaction.to_tracestate()
        if set_by == "get_trace_context":
            transaction.get_trace_context()

    assert transaction._sentry_tracestate == "sentry=doGsaREgReaT"

    # user data would be included in tracestate if it were recomputed at this point
    sentry_sdk.set_user({"id": 12312013, "segment": "bigs"})

    # value hasn't changed
    assert transaction._sentry_tracestate == "sentry=doGsaREgReaT"


@pytest.mark.parametrize("sampled", [True, False, None])
def test_to_traceparent(sentry_init, sampled):

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
        sampled=sampled,
    )

    traceparent = transaction.to_traceparent()

    trace_id, parent_span_id, parent_sampled = traceparent.split("-")
    assert trace_id == "12312012123120121231201212312012"
    assert parent_span_id == transaction.span_id
    assert parent_sampled == (
        "1" if sampled is True else "0" if sampled is False else ""
    )


def test_to_tracestate(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    # it correctly uses the value from the transaction itself or the span's
    # containing transaction
    transaction_no_third_party = Transaction(
        trace_id="12312012123120121231201212312012",
        sentry_tracestate="sentry=doGsaREgReaT",
    )
    non_orphan_span = Span()
    non_orphan_span._containing_transaction = transaction_no_third_party
    assert transaction_no_third_party.to_tracestate() == "sentry=doGsaREgReaT"
    assert non_orphan_span.to_tracestate() == "sentry=doGsaREgReaT"

    # it combines sentry and third-party values correctly
    transaction_with_third_party = Transaction(
        trace_id="12312012123120121231201212312012",
        sentry_tracestate="sentry=doGsaREgReaT",
        third_party_tracestate="maisey=silly",
    )
    assert (
        transaction_with_third_party.to_tracestate()
        == "sentry=doGsaREgReaT,maisey=silly"
    )

    # it computes a tracestate from scratch for orphan transactions
    orphan_span = Span(
        trace_id="12312012123120121231201212312012",
    )
    assert orphan_span._containing_transaction is None
    assert orphan_span.to_tracestate() == "sentry=" + compute_tracestate_value(
        {
            "trace_id": "12312012123120121231201212312012",
            "environment": "dogpark",
            "release": "off.leash.park",
            "public_key": "dogsarebadatkeepingsecrets",
        }
    )


@pytest.mark.parametrize("sampling_decision", [True, False])
def test_sentrytrace_extraction(sampling_decision):
    sentrytrace_header = "12312012123120121231201212312012-0415201309082013-{}".format(
        1 if sampling_decision is True else 0
    )
    assert extract_sentrytrace_data(sentrytrace_header) == {
        "trace_id": "12312012123120121231201212312012",
        "parent_span_id": "0415201309082013",
        "parent_sampled": sampling_decision,
    }


@pytest.mark.parametrize(
    ("incoming_header", "expected_sentry_value", "expected_third_party"),
    [
        # sentry only
        ("sentry=doGsaREgReaT", "sentry=doGsaREgReaT", None),
        # sentry only, invalid (`!` isn't a valid base64 character)
        ("sentry=doGsaREgReaT!", None, None),
        # stuff before
        ("maisey=silly,sentry=doGsaREgReaT", "sentry=doGsaREgReaT", "maisey=silly"),
        # stuff after
        ("sentry=doGsaREgReaT,maisey=silly", "sentry=doGsaREgReaT", "maisey=silly"),
        # stuff before and after
        (
            "charlie=goofy,sentry=doGsaREgReaT,maisey=silly",
            "sentry=doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple before
        (
            "charlie=goofy,maisey=silly,sentry=doGsaREgReaT",
            "sentry=doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple after
        (
            "sentry=doGsaREgReaT,charlie=goofy,maisey=silly",
            "sentry=doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple before and after
        (
            "charlie=goofy,maisey=silly,sentry=doGsaREgReaT,bodhi=floppy,cory=loyal",
            "sentry=doGsaREgReaT",
            "charlie=goofy,maisey=silly,bodhi=floppy,cory=loyal",
        ),
        # only third-party data
        ("maisey=silly", None, "maisey=silly"),
        # invalid third-party data, valid sentry data
        ("maisey_is_silly,sentry=doGsaREgReaT", "sentry=doGsaREgReaT", None),
        # valid third-party data, invalid sentry data
        ("maisey=silly,sentry=doGsaREgReaT!", None, "maisey=silly"),
        # nothing valid at all
        ("maisey_is_silly,sentry=doGsaREgReaT!", None, None),
    ],
)
def test_tracestate_extraction(
    incoming_header, expected_sentry_value, expected_third_party
):
    assert extract_tracestate_data(incoming_header) == {
        "sentry_tracestate": expected_sentry_value,
        "third_party_tracestate": expected_third_party,
    }


def test_iter_headers(sentry_init, monkeypatch):
    monkeypatch.setattr(
        Transaction,
        "to_traceparent",
        mock.Mock(return_value="12312012123120121231201212312012-0415201309082013-0"),
    )
    monkeypatch.setattr(
        Transaction,
        "to_tracestate",
        mock.Mock(return_value="sentry=doGsaREgReaT,charlie=goofy"),
    )

    transaction = Transaction(
        name="/interactions/other-dogs/new-dog",
        op="greeting.sniff",
    )

    headers = dict(transaction.iter_headers())
    assert (
        headers["sentry-trace"] == "12312012123120121231201212312012-0415201309082013-0"
    )
    assert headers["tracestate"] == "sentry=doGsaREgReaT,charlie=goofy"


@pytest.mark.parametrize(
    "data",
    [  # comes out with no trailing `=`
        {"name": "Maisey", "birthday": "12/31/12"},
        # comes out with one trailing `=`
        {"dogs": "yes", "cats": "maybe"},
        # comes out with two trailing `=`
        {"name": "Charlie", "birthday": "11/21/12"},
    ],
)
def test_tracestate_reinflation(data):
    encoded_tracestate = to_base64(json.dumps(data)).strip("=")
    assert reinflate_tracestate(encoded_tracestate) == data
