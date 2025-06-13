# Langchain Integration Redundant Callbacks Bug Reproduction

This directory contains a comprehensive reproduction of the Sentry Langchain integration bug where redundant callbacks are created when a user manually configures a `SentryLangchainCallback` instance.

## Issue Description

When configuring a `SentryLangchainCallback` instance manually and passing it to a Langchain model's `invoke` call, the Sentry integration creates another instance of the same callback. This results in:

1. **Performance duplication**: Events being tracked twice in spans/transactions
2. **Error duplication**: Issues being reported twice to Sentry's Issues dashboard
3. **KeyError crashes**: Race conditions due to shared span_map causing internal SDK errors

## 🚨 CRITICAL: Two Interconnected Bugs Discovered

### Bug 1: Callback Duplication (Original Issue)

The `_wrap_configure` function fails to detect existing callbacks in `args[1]` (inheritable_callbacks), leading to duplicate callback instances.

### Bug 2: Shared span_map (Newly Discovered)

The `SentryLangchainCallback.span_map` is declared as a **class variable**, causing ALL instances to share the same span storage. This creates race conditions when multiple callbacks exist.

**Combined Effect**: The callback duplication bug triggers the span_map race condition, causing KeyErrors and preventing proper telemetry collection.

## What This Reproduction Shows

This enhanced reproduction includes **three test cases**:

1. **Successful Request Test**: Demonstrates duplicate spans in Sentry Performance monitoring
2. **Failing Request Test**: Demonstrates duplicate issues in Sentry Issues dashboard
3. **KeyError Investigation**: Shows the span_map class variable race condition

## Prerequisites

- Python 3.8 or higher
- pip package manager
- (Optional) A real Sentry project DSN to see the duplication in your dashboard

## Step-by-Step Instructions

### 1. Create a Virtual Environment (Recommended)

```bash
# Create a new virtual environment
python3 -m venv venv

# Activate the virtual environment
# On Linux/Mac:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate
```

### 2. Install Dependencies

```bash
# Install the required packages
pip install -r requirements.txt
```

**Note**: The reproduction script uses the local `sentry_sdk` from the parent directory. If you want to run this reproduction standalone, remove the `sys.path.insert` line from `reproduce_issue.py` and ensure `sentry-sdk==2.27.0` is installed.

### 3. (Optional) Set Your Sentry DSN

To see the actual duplication in your Sentry dashboard:

```bash
# Set your real Sentry DSN
export SENTRY_DSN="https://your-dsn@sentry.io/your-project-id"
```

If you don't set this, the script will use a dummy DSN and you'll only see the console output demonstrating the bug.

### 4. Run the Reproduction Scripts

#### Main Reproduction (shows callback duplication):

```bash
python reproduce_issue.py
```

#### KeyError Investigation (shows span_map race condition):

```bash
python demonstrate_keyerror.py
```

### 5. Observe the Output and Sentry Dashboard

#### Console Output

**reproduce_issue.py** will show:

```
🔍 Inspecting callbacks in MockChatModel._generate:
   Handlers: 2 total
   ➜ Found SentryLangchainCallback in handlers (id: 140445566088464)
   ➜ Found SentryLangchainCallback in handlers (id: 140445577607568)

📊 Total UNIQUE SentryLangchainCallback instances: 2
❌ BUG REPRODUCED: Multiple SentryLangchainCallback instances found!

[sentry] ERROR: Internal error in sentry_sdk
KeyError: UUID('4b537014-b30e-472a-b1a0-4075d5de1f97')
```

**demonstrate_keyerror.py** will show:

```
🚨 CONFIRMED: Both callbacks share the same span_map!
🚨 This is a class variable, not an instance variable

🚨 Callback 2 failed with KeyError: [UUID]
🚨 This is exactly what we see in the actual error!
```

#### Why You Might Not See Duplicate Spans/Issues

The reason duplicate spans/issues may not appear in Sentry is because:

1. **Race Condition**: The second callback overwrites the first callback's span entry
2. **KeyError**: The second callback crashes before completing its work
3. **Cleanup Interference**: Both callbacks interfere with each other's cleanup process

## Root Cause Analysis

### Bug 1: Callback Duplication

The `_wrap_configure` function in `sentry_sdk/integrations/langchain.py` checks for existing callbacks in:

- `kwargs["local_callbacks"]`
- `args[2]` (local_callbacks parameter)

But it **misses** checking `args[1]` (inheritable_callbacks parameter), which is where user-provided callbacks from `RunnableConfig` are actually placed.

### Bug 2: Shared span_map

The `SentryLangchainCallback` class declares `span_map` as a class variable:

```python
class SentryLangchainCallback(BaseCallbackHandler):
    span_map = OrderedDict()  # ← CLASS VARIABLE! All instances share this
```

This should be an instance variable to prevent race conditions between multiple callback instances.

## Expected Results

### With Both Bugs (Current Behavior)

- **Console**: Shows 2 unique SentryLangchainCallback instances
- **Console**: Shows KeyError in Sentry debug output
- **Sentry Performance**: May see no spans or single spans (due to race condition)
- **Sentry Issues**: May see no duplicate issues (due to KeyError preventing reporting)

### After Both Fixes (Expected Behavior)

- **Console**: Shows 1 unique SentryLangchainCallback instance
- **Console**: No KeyErrors in debug output
- **Sentry Performance**: Single span per LLM operation
- **Sentry Issues**: Single error event per failed operation

## Impact in Production

These bugs cause:

- **Silent failures** in telemetry collection due to KeyErrors
- **Incomplete span tracking** due to race conditions
- **Missing error reports** when the second callback crashes
- **Unpredictable behavior** depending on callback execution timing
- **SDK instability** with internal errors appearing in logs

## Files in this Directory

- `reproduce_issue.py` - Main reproduction script showing callback duplication and KeyError
- `demonstrate_keyerror.py` - Detailed investigation of the span_map race condition
- `requirements.txt` - Python dependencies needed to run the reproduction
- `proposed_fix.py` - Shows the problematic code and the proposed fix for Bug 1
- `README.md` - This file

## Complete Fix Required

Both bugs need to be fixed:

1. **Fix callback duplication**: Check `args[1]` in `_wrap_configure` function
2. **Fix span_map sharing**: Change `span_map` from class variable to instance variable:
   ```python
   def __init__(self, max_span_map_size, include_prompts, tiktoken_encoding_name=None):
       self.span_map = OrderedDict()  # Instance variable instead of class variable
       # ... rest of init
   ```
