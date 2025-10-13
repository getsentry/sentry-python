import sentry_sdk
from sentry_sdk.performance import allow_n_plus_one


def test_allow_n_plus_one_sets_tag(sentry_init):
    # Initialize SDK with test fixture
    sentry_init()

    with sentry_sdk.start_transaction(name="tx") as tx:
        with allow_n_plus_one("expected"):
            # no-op loop simulated
            pass

        # The tag should be set on the transaction
        assert tx._tags.get("sentry.n_plus_one.ignore") is True
        assert tx._tags.get("sentry.n_plus_one.reason") == "expected"
