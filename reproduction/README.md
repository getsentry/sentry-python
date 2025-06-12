# Langchain Integration Redundant Callbacks Bug Reproduction

This directory contains a minimal reproduction of the Sentry Langchain integration bug where redundant callbacks are created when a user manually configures a `SentryLangchainCallback` instance.

## Issue Description

When configuring a `SentryLangchainCallback` instance manually and passing it to a Langchain model's `invoke` call, the Sentry integration creates another instance of the same callback. This results in events being processed twice.

## Prerequisites

- Python 3.8 or higher
- pip package manager

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

### 3. Run the Reproduction Script

```bash
python reproduce_issue.py
```

### 4. Observe the Output

The script will output detailed information about the callbacks. Look for the section labeled "🔍 Inspecting callbacks in MockChatModel._generate:".

**Expected behavior**: Only one `SentryLangchainCallback` instance should exist.

**Actual behavior (bug)**: You will see **TWO** different `SentryLangchainCallback` instances with different IDs:

```
🔍 Inspecting callbacks in MockChatModel._generate:
   Handlers: 2 total
   Inheritable handlers: 1 total
   ➜ Found SentryLangchainCallback in handlers (id: 140445566088464)
   ➜ Found SentryLangchainCallback in handlers (id: 140445577607568)
   ➜ Found SentryLangchainCallback in inheritable_handlers (id: 140445566088464)

📊 Total UNIQUE SentryLangchainCallback instances: 2
❌ BUG REPRODUCED: Multiple SentryLangchainCallback instances found!
   This means events will be processed multiple times.
```

## What the Script Does

1. Initializes Sentry SDK with the Langchain integration
2. Creates a mock chat model (to avoid needing AWS credentials)
3. Creates a manual `SentryLangchainCallback` instance with custom configuration
4. Passes this callback via `RunnableConfig` to the model's `invoke` method
5. Inspects the callbacks that are actually used during execution

## Root Cause

The bug occurs in the `_wrap_configure` function in `sentry_sdk/integrations/langchain.py`. The function checks for existing callbacks in:
- `kwargs["local_callbacks"]`
- `args[2]` (local_callbacks parameter)

But it misses checking `args[1]` (inheritable_callbacks parameter), which is where user-provided callbacks from `RunnableConfig` are actually placed.

## Impact

This bug causes:
- Events to be sent to Sentry twice
- Potentially different configurations between the two callbacks (the manual one has user's settings, the automatic one has defaults)
- Increased processing overhead
- Potential confusion in Sentry dashboards due to duplicate events

## Files in this Directory

- `reproduce_issue.py` - The main reproduction script
- `requirements.txt` - Python dependencies needed to run the reproduction
- `proposed_fix.py` - Shows the problematic code and the proposed fix
- `README.md` - This file