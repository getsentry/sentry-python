import pytest

from sentry_sdk.tracing import Transaction


@pytest.mark.parametrize(
    ("sentry_value, third_party_value, expected_tracestate"),
    [
        ("doGsaREgReaT", None, "sentry=doGsaREgReaT"),
        (
            "doGsaREgReaT",
            "maisey=silly",
            "sentry=doGsaREgReaT,maisey=silly",
        ),
        # this should never happen, but this proves things won't blow up if it does
        (None, "charlie=goofy", "sentry=e30,charlie=goofy"),
    ],
)
def test_to_tracestate(
    sentry_init, sentry_value, third_party_value, expected_tracestate
):
    sentry_init(
        dsn="https://dogsarebadatkeepingsecrets@squirrelchasers.ingest.sentry.io/12312012",
        environment="dogpark",
        release="off.leash.park",
    )

    transaction = Transaction(
        name="/meet/other-dog/new",
        op="greeting.sniff",
        third_party_tracestate=third_party_value,
    )

    # we have to do this separately (rather than passing it to the Transaction
    # constructor) because otherwise the constructor will come up with its own
    # sentry_tracestate_value in the case where the one passed in the test is None
    transaction._sentry_tracestate_value = sentry_value

    assert transaction.to_tracestate() == expected_tracestate
