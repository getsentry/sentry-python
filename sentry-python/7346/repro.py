"""
Reproduction for getsentry/sentry-python#7346.

`_experiments={"data_collection": None}` leaves the client in a state where
neither side of the option is active:

* `has_data_collection_enabled()` (sentry_sdk/utils.py) gates on **key
  presence**, so it returns True.
* `_resolve_data_collection()` (sentry_sdk/data_collection.py) only treats a
  **non-None** value as user provided, so the resolved config falls back to the
  legacy `send_default_pii` mapping with `provided_by_user=False`.

Two observable consequences, each printed as a table below:

1. The default `EventScrubber` is never installed, so `extra` values like
   `password` are sent unfiltered.
2. WSGI request attributes gain `http.query` / `url.path` / `url.full`, which
   are not attached when `data_collection` is left unset.

Set SENTRY_DSN if you also want the events delivered to a real project:

    export SENTRY_DSN=...
"""

import os
import warnings

import sentry_sdk
from sentry_sdk.integrations.wsgi import _get_request_attributes
from sentry_sdk.scrubber import EventScrubber
from sentry_sdk.utils import has_data_collection_enabled

DSN = os.environ.get("SENTRY_DSN") or ""

# Sentinel telling the repro to omit `_experiments` entirely, which is
# different from passing `{"data_collection": None}`.
UNSET = object()

SCENARIOS = [
    ("no _experiments (baseline)", UNSET),
    ("data_collection={} (explicit opt in)", {}),
    ("data_collection=None (the bug)", None),
]

WSGI_ENVIRON = {
    "REQUEST_METHOD": "GET",
    "PATH_INFO": "/checkout",
    "QUERY_STRING": "email=a%40b.com&coupon=SAVE10",
    "SERVER_NAME": "localhost",
    "SERVER_PORT": "8000",
    "wsgi.url_scheme": "http",
}


def init(data_collection):
    experiments = (
        {} if data_collection is UNSET else {"data_collection": data_collection}
    )
    captured = []

    def before_send(event, hint):
        # `before_send` runs after the event scrubber, so whatever lands here is
        # what would have been sent to Sentry.
        captured.append(event)
        return None if not DSN else event

    sentry_sdk.init(
        dsn=DSN,
        _experiments=experiments,
        before_send=before_send,
    )
    return captured


def scrubber_row(data_collection):
    captured = init(data_collection)

    scope = sentry_sdk.get_isolation_scope()
    scope.set_extra("password", "hunter2")
    scope.set_extra("token", "abc123")

    sentry_sdk.capture_message("hi")
    sentry_sdk.flush()

    options = sentry_sdk.get_client().options
    extra = captured[0].get("extra", {}) if captured else {}
    return (
        extra.get("password"),
        extra.get("token"),
        options["event_scrubber"] is not None,
    )


def gate_row(data_collection):
    init(data_collection)
    options = sentry_sdk.get_client().options
    return (
        has_data_collection_enabled(options),
        options["data_collection"]["provided_by_user"],
    )


def request_attributes_row(data_collection):
    init(data_collection)
    attributes = _get_request_attributes(WSGI_ENVIRON)
    return (
        attributes.get("http.query"),
        attributes.get("url.path"),
        attributes.get("url.full"),
    )


def explicit_scrubber_warning():
    """An explicitly configured `event_scrubber` is discarded with a warning
    that says data collection configuration was provided, which it was not."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sentry_sdk.init(
            dsn=DSN,
            _experiments={"data_collection": None},
            event_scrubber=EventScrubber(denylist=["my_secret"]),
        )
    scrubber = sentry_sdk.get_client().options["event_scrubber"]
    return scrubber, [str(w.message) for w in caught]


def table(title, headers, rows):
    print(f"\n{title}")
    widths = [
        max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))
    ]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*(str(cell) for cell in row)))


def main():
    gate_rows = []
    scrubber_rows = []
    attribute_rows = []
    for label, data_collection in SCENARIOS:
        gate_rows.append((label, *gate_row(data_collection)))
        password, token, has_scrubber = scrubber_row(data_collection)
        scrubber_rows.append((label, password, token, has_scrubber))
        attribute_rows.append((label, *request_attributes_row(data_collection)))

    table(
        "0. The two functions disagree",
        ("config", "has_data_collection_enabled()", "provided_by_user"),
        gate_rows,
    )
    print(
        "\n   `data_collection=None` is the only config where the two disagree: "
        "\n   every caller of `has_data_collection_enabled()` takes the data "
        "\n   collection branch, but the resolved config is the legacy "
        "\n   `send_default_pii` mapping."
    )

    table(
        "1. Event scrubbing",
        ("config", "extra[password]", "extra[token]", "event_scrubber installed"),
        scrubber_rows,
    )
    print(
        "\n   Expected: `data_collection=None` filters like the baseline, because "
        "\n   `_resolve_data_collection` did not treat it as user provided."
        "\n   Actual:   the scrubber is skipped and the raw secrets are sent."
    )

    table(
        "2. WSGI request attributes",
        ("config", "http.query", "url.path", "url.full"),
        attribute_rows,
    )
    print(
        "\n   Expected: `data_collection=None` attaches no query params, like the "
        "\n   baseline, since `should_send_default_pii()` is False."
        "\n   Actual:   the data collection branch is taken and the query string is "
        "\n   attached."
    )

    scrubber, messages = explicit_scrubber_warning()
    print("\n3. Explicitly configured event_scrubber")
    print(f'   options["event_scrubber"] after init: {scrubber}')
    for message in messages:
        print(f"   warning: {message}")
    print(
        "\n   Expected: no warning, because no data collection configuration was "
        "\n   provided; the explicit scrubber should be kept."
        "\n   Actual:   the scrubber is dropped and the warning misreports why."
    )

    _, password, token, has_scrubber = scrubber_rows[-1]
    _, http_query, _, _ = attribute_rows[-1]
    reproduced = (
        password == "hunter2"
        and token == "abc123"
        and not has_scrubber
        and http_query is not None
    )
    print(f"\nBug reproduced: {reproduced}")
    return 0 if reproduced else 1


if __name__ == "__main__":
    raise SystemExit(main())
