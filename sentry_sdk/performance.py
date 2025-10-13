from contextlib import contextmanager
import sentry_sdk


@contextmanager
def allow_n_plus_one(reason=None):
    """Context manager to mark the current transaction/spans as allowed N+1.

    This sets a tag on the current transaction and current span so the server
    side N+1 detector can (optionally) ignore this transaction. The server
    change is required for ignoring to take effect; this helper simply
    attaches metadata to the event.

    Usage:
        with allow_n_plus_one("expected loop"):
            for x in queryset:
                ...
    """
    tx = sentry_sdk.get_current_span()
    if tx is not None:
        try:
            tx.set_tag("sentry.n_plus_one.ignore", True)
            if reason:
                tx.set_tag("sentry.n_plus_one.reason", reason)
        except Exception:
            # best-effort
            pass

    try:
        yield
    finally:
        # leave the tag in place so the transaction contains it when sent
        pass
