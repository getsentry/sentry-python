# ClickHouse-Driver Generator Issue Reproduction

This directory contains a minimal reproduction for the Sentry SDK issue #4657:
https://github.com/getsentry/sentry-python/issues/4657

## Issue Summary

When using a generator as a data source for INSERT queries with clickhouse-driver,
the Sentry SDK's clickhouse-driver integration consumes the generator before it 
reaches clickhouse-driver, resulting in no data being inserted.

The bug occurs when `send_default_pii=True` is set, causing the integration to
call `db_params.extend(data)` which exhausts the generator.

## Setup

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Reproduction

### Option 1: Simple Reproduction (Recommended)
This shows the core issue clearly:

```bash
python simple_reproduce.py
```

Expected output:
- TEST 1 (Generator): Shows the generator being consumed by Sentry, leaving 0 items for clickhouse-driver
- TEST 2 (List): Shows that lists work correctly since they can be consumed multiple times

### Option 2: Comprehensive Test
This includes multiple test scenarios:

```bash
python reproduce_issue.py
```

This script will:
1. Test without Sentry SDK (works correctly)
2. Test with Sentry SDK (fails - demonstrates the bug)
3. Show the exact traceback scenario from the issue

## Key Code Location

The bug is in `/workspace/sentry_sdk/integrations/clickhouse_driver.py` at lines 141-143:

```python
if should_send_default_pii():
    db_params = span._data.get("db.params", [])
    db_params.extend(data)  # <-- This consumes the generator!
    span.set_data("db.params", db_params)
```

## Workarounds

Until this is fixed, you can:

1. **Disable PII**: Set `send_default_pii=False` in `sentry_sdk.init()`
2. **Use lists instead of generators**: Convert generators to lists before passing to `execute()`
3. **Disable the integration**: Remove `ClickhouseDriverIntegration()` from your Sentry config

## Expected Fix

The integration should check if `data` is a generator and handle it appropriately,
possibly by:
- Not consuming generators when storing params
- Converting to a reusable iterator (like `itertools.tee`)
- Only storing a sample of the data rather than all of it