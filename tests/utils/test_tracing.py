import pytest

from sentry_sdk.tracing import Transaction
from sentry_sdk.tracing_utils import extract_sentrytrace_data, extract_tracestate_data


def test_sentrytrace_extraction():
    assert extract_sentrytrace_data(
        "12312012123120121231201212312012-0415201309082013-0"
    ) == {
        "trace_id": "12312012123120121231201212312012",
        "parent_span_id": "0415201309082013",
        "parent_sampled": False,
    }


@pytest.mark.parametrize(
    ("incoming_header", "expected_sentry_value", "expected_third_party"),
    [
        # sentry only
        ("sentry=doGsaREgReaT", "doGsaREgReaT", None),
        # sentry only, invalid (`!` isn't a valid base64 character)
        ("sentry=doGsaREgReaT!", None, None),
        # stuff before
        ("maisey=silly,sentry=doGsaREgReaT", "doGsaREgReaT", "maisey=silly"),
        # stuff after
        ("sentry=doGsaREgReaT,maisey=silly", "doGsaREgReaT", "maisey=silly"),
        # stuff before and after
        (
            "charlie=goofy,sentry=doGsaREgReaT,maisey=silly",
            "doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple before
        (
            "charlie=goofy,maisey=silly,sentry=doGsaREgReaT",
            "doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple after
        (
            "sentry=doGsaREgReaT,charlie=goofy,maisey=silly",
            "doGsaREgReaT",
            "charlie=goofy,maisey=silly",
        ),
        # multiple before and after
        (
            "charlie=goofy,maisey=silly,sentry=doGsaREgReaT,bodhi=floppy,cory=loyal",
            "doGsaREgReaT",
            "charlie=goofy,maisey=silly,bodhi=floppy,cory=loyal",
        ),
        # only third party data
        ("maisey=silly", None, "maisey=silly"),
        # invalid third party data, valid sentry data
        ("maisey_is_silly,sentry=doGsaREgReaT", "doGsaREgReaT", None),
        # valid third party data, invalid sentry data
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


def test_tracestate_computation(sentry_init):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/meet/other-dog/new",
        op="greeting.sniff",
        trace_id="12312012123120121231201212312012",
    )
    assert transaction._sentry_tracestate_value == (
        "eyJ0cmFjZV9pZCI6ICIxMjMxMjAxMjEyMzEyMDEyMTIzMTIwMTIxMjMxMjAxMiIsICJl"
        + "bnZpcm9ubWVudCI6ICJkb2dwYXJrIiwgInJlbGVhc2UiOiAib2ZmLmxlYXNoLnBhcm"
        + "siLCAicHVibGljX2tleSI6ICJkb2dzYXJlYmFkYXRrZWVwaW5nc2VjcmV0cyJ9"
    )
