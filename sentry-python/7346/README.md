# Reproduction for sentry-python#7346

**Issue:** https://github.com/getsentry/sentry-python/issues/7346

## Description

`_experiments={"data_collection": None}` type checks, because `data_collection` is
typed `Optional[DataCollectionUserOptions]`, but the two functions that read the
option disagree about what `None` means:

- `has_data_collection_enabled()` (`sentry_sdk/utils.py:2108`) gates on **key
  presence**: `"data_collection" in options.get("_experiments", {})` → `True`
- `_resolve_data_collection()` (`sentry_sdk/data_collection.py:287`) only treats a
  **non-`None`** value as user provided: `if user_dc is not None` → `provided_by_user=False`

So `data_collection=None` lands in a state where neither side of the option is
active: every caller of `has_data_collection_enabled()` takes the data collection
branch, while the resolved config is the legacy `send_default_pii=False` mapping.

## Steps to Reproduce

```bash
uv run repro.py
```

The script runs against this checkout (`[tool.uv.sources]` points at `../..`),
because `data_collection` is an unreleased `_experiments` option. No DSN is
needed — events are inspected in `before_send`. Set `export SENTRY_DSN=...` if
you also want them delivered.

It exits `0` when the bug reproduces and `1` when it is fixed.

## Expected Behavior

`data_collection=None` should behave like one of its two neighbours, not fall
between them:

- the default `EventScrubber` stays installed, so `extra["password"]` is `[Filtered]`
- an explicitly configured `event_scrubber` is kept, with no warning
- WSGI request attributes attach no `http.query` / `url.path` / `url.full`, since
  `should_send_default_pii()` is `False`

## Actual Behavior

```
0. The two functions disagree
config                                has_data_collection_enabled()  provided_by_user
------------------------------------  -----------------------------  ----------------
no _experiments (baseline)            False                          False
data_collection={} (explicit opt in)  True                           True
data_collection=None (the bug)        True                           False

1. Event scrubbing
config                                extra[password]  extra[token]  event_scrubber installed
------------------------------------  ---------------  ------------  ------------------------
no _experiments (baseline)            [Filtered]       [Filtered]    True
data_collection={} (explicit opt in)  hunter2          abc123        False
data_collection=None (the bug)        hunter2          abc123        False

2. WSGI request attributes
config                                http.query                     url.path   url.full
------------------------------------  -----------------------------  ---------  ------------------------------------------------------------
no _experiments (baseline)            None                           None       None
data_collection={} (explicit opt in)  email=a%40b.com&coupon=SAVE10  /checkout  http://localhost:8000/checkout?email=a%40b.com&coupon=SAVE10
data_collection=None (the bug)        email=a%40b.com&coupon=SAVE10  /checkout  http://localhost:8000/checkout?email=a%40b.com&coupon=SAVE10

3. Explicitly configured event_scrubber
   options["event_scrubber"] after init: None
   warning: Event scrubbers are not enabled when data collection configuration is provided. Ignoring event_scrubber...

Bug reproduced: True
```

## Note on the issue's scrubbing table

The issue reports `extra["password"] == "[Filtered]"` for `data_collection={}`.
On this checkout that config also yields `"hunter2"`, because skipping the
`EventScrubber` is the intended behaviour whenever data collection is configured
(`sentry_sdk/client.py:357`). That does not affect the report: the divergence is
between `has_data_collection_enabled()` and `provided_by_user`, which section 0
shows directly, and `data_collection=None` is the only config where they disagree.

## Environment

- Python: 3.14.5 (the script supports >= 3.9)
- sentry-sdk: this checkout (2.69.1 at the time of writing)
- OS: macOS
