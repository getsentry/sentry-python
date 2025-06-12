"""
Proposed fix for the Langchain callback duplication issue.
This shows the changes needed in sentry_sdk/integrations/langchain.py
"""

# The current problematic code in _wrap_configure function:
def _wrap_configure_CURRENT(f):
    """Current implementation that has the bug"""
    @wraps(f)
    def new_configure(*args, **kwargs):
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(*args, **kwargs)

        with capture_internal_exceptions():
            new_callbacks = []
            if "local_callbacks" in kwargs:
                existing_callbacks = kwargs["local_callbacks"]
                kwargs["local_callbacks"] = new_callbacks
            elif len(args) > 2:
                existing_callbacks = args[2]  # Only checks args[2]!
                args = (args[0], args[1], new_callbacks) + args[3:]
            else:
                existing_callbacks = []

            # ... code to copy existing callbacks ...

            # Check if callback already exists (but only in the copied callbacks)
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


# The FIXED implementation:
def _wrap_configure_FIXED(f):
    """Fixed implementation that checks all callback locations"""
    @wraps(f)
    def new_configure(*args, **kwargs):
        integration = sentry_sdk.get_client().get_integration(LangchainIntegration)
        if integration is None:
            return f(*args, **kwargs)

        with capture_internal_exceptions():
            # Check if SentryLangchainCallback already exists in ANY of the callback lists
            already_added = False
            
            # Check in kwargs["local_callbacks"]
            if "local_callbacks" in kwargs:
                callbacks = kwargs.get("local_callbacks", [])
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break
            
            # Check in args[1] (inheritable_callbacks) - THIS IS THE KEY FIX!
            if not already_added and len(args) > 1 and args[1] is not None:
                callbacks = args[1]
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break
                elif isinstance(callbacks, BaseCallbackHandler) and isinstance(callbacks, SentryLangchainCallback):
                    already_added = True
            
            # Check in args[2] (local_callbacks)
            if not already_added and len(args) > 2 and args[2] is not None:
                callbacks = args[2]
                if isinstance(callbacks, list):
                    for cb in callbacks:
                        if isinstance(cb, SentryLangchainCallback):
                            already_added = True
                            break
                elif isinstance(callbacks, BaseCallbackHandler) and isinstance(callbacks, SentryLangchainCallback):
                    already_added = True
            
            # Only add a new callback if none was found
            if not already_added:
                new_callbacks = []
                
                # ... rest of the code to handle callbacks ...
                
                new_callbacks.append(
                    SentryLangchainCallback(
                        integration.max_spans,
                        integration.include_prompts,
                        integration.tiktoken_encoding_name,
                    )
                )
        
        return f(*args, **kwargs)
    return new_configure


# Key changes in the fix:
# 1. Check ALL possible callback locations before deciding to add a new callback
# 2. Specifically check args[1] (inheritable_callbacks) where user callbacks are placed
# 3. Only proceed with adding a new callback if none exists in any location