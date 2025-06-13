"""
Proposed fix for the Langchain callback duplication issue.
This shows the changes needed in sentry_sdk/integrations/langchain.py

The issue is in the _wrap_configure function around line 415.
"""

from functools import wraps
from typing import Any, List
from sentry_sdk.integrations.langchain import (
    SentryLangchainCallback,
    LangchainIntegration,
)
from langchain_core.callbacks import BaseCallbackHandler
import sentry_sdk
from sentry_sdk.utils import capture_internal_exceptions, logger

# ============================================================================
# CURRENT PROBLEMATIC CODE (from _wrap_configure function)
# ============================================================================


def _wrap_configure_CURRENT(f):
    """Current implementation that has the bug"""

    @wraps(f)
    def new_configure(*args, **kwargs):
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(*args, **kwargs)

        with capture_internal_exceptions():
            new_callbacks = []  # type: List[BaseCallbackHandler]

            # Handle local_callbacks from kwargs or args[2]
            if "local_callbacks" in kwargs:
                existing_callbacks = kwargs["local_callbacks"]
                kwargs["local_callbacks"] = new_callbacks
            elif len(args) > 2:
                existing_callbacks = args[2]  # ❌ ONLY checks args[2]!
                args = (
                    args[0],
                    args[1],
                    new_callbacks,
                ) + args[3:]
            else:
                existing_callbacks = []

            # Copy existing callbacks to new_callbacks
            if existing_callbacks:
                if isinstance(existing_callbacks, list):
                    for cb in existing_callbacks:
                        new_callbacks.append(cb)
                elif isinstance(existing_callbacks, BaseCallbackHandler):
                    new_callbacks.append(existing_callbacks)
                else:
                    logger.debug("Unknown callback type: %s", existing_callbacks)

            # ❌ BUG: Only checks callbacks that were copied to new_callbacks
            # Misses callbacks in args[1] (inheritable_callbacks)!
            already_added = False
            for callback in new_callbacks:
                if isinstance(callback, SentryLangchainCallback):
                    already_added = True

            if not already_added:
                new_callbacks.append(
                    SentryLangchainCallback(
                        integration.max_spans,
                        integration.include_prompts,
                        integration.tiktoken_encoding_name,
                    )
                )
        return f(*args, **kwargs)

    return new_configure


# ============================================================================
# FIXED IMPLEMENTATION
# ============================================================================


def _wrap_configure_FIXED(f):
    """Fixed implementation that checks all callback locations"""

    @wraps(f)
    def new_configure(*args, **kwargs):
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(*args, **kwargs)

        with capture_internal_exceptions():
            # ✅ FIX: Check if SentryLangchainCallback already exists in ANY location
            already_added = False

            # Check in kwargs["local_callbacks"]
            if "local_callbacks" in kwargs:
                callbacks = kwargs.get("local_callbacks", [])
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break

            # ✅ KEY FIX: Check in args[1] (inheritable_callbacks)
            # This is where RunnableConfig callbacks are placed!
            if not already_added and len(args) > 1 and args[1] is not None:
                callbacks = args[1]
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break
                elif isinstance(callbacks, BaseCallbackHandler) and isinstance(
                    callbacks, SentryLangchainCallback
                ):
                    already_added = True

            # Check in args[2] (local_callbacks)
            if not already_added and len(args) > 2 and args[2] is not None:
                callbacks = args[2]
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break
                elif isinstance(callbacks, BaseCallbackHandler) and isinstance(
                    callbacks, SentryLangchainCallback
                ):
                    already_added = True

            # Only add a new callback if none was found in ANY location
            if not already_added:
                new_callbacks = []  # type: List[BaseCallbackHandler]

                # Preserve existing callbacks from local_callbacks
                if "local_callbacks" in kwargs:
                    existing_callbacks = kwargs["local_callbacks"]
                    kwargs["local_callbacks"] = new_callbacks
                elif len(args) > 2:
                    existing_callbacks = args[2]
                    args = (
                        args[0],
                        args[1],
                        new_callbacks,
                    ) + args[3:]
                else:
                    existing_callbacks = []

                # Copy existing callbacks
                if existing_callbacks:
                    if isinstance(existing_callbacks, list):
                        for cb in existing_callbacks:
                            new_callbacks.append(cb)
                    elif isinstance(existing_callbacks, BaseCallbackHandler):
                        new_callbacks.append(existing_callbacks)
                    else:
                        logger.debug("Unknown callback type: %s", existing_callbacks)

                # Add our callback
                new_callbacks.append(
                    SentryLangchainCallback(
                        integration.max_spans,
                        integration.include_prompts,
                        integration.tiktoken_encoding_name,
                    )
                )

        return f(*args, **kwargs)

    return new_configure


# ============================================================================
# SUMMARY OF CHANGES
# ============================================================================

"""
Key changes in the fix:

1. EARLY DETECTION: Check for existing SentryLangchainCallback instances 
   BEFORE modifying any callback lists

2. COMPREHENSIVE CHECKING: Check ALL possible callback locations:
   - kwargs["local_callbacks"]
   - args[1] (inheritable_callbacks) ← THE MISSING CHECK!
   - args[2] (local_callbacks)

3. PROPER LOGIC: Only create and add a new callback if NO existing 
   SentryLangchainCallback is found in any location

The original bug occurred because the function only checked args[2] for 
existing callbacks, but user-provided callbacks from RunnableConfig 
are actually placed in args[1] (inheritable_callbacks).

This fix ensures that manually configured SentryLangchainCallback 
instances are properly detected and respected, preventing duplication.
"""
