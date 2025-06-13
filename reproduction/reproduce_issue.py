#!/usr/bin/env python
"""
Script to reproduce the Langchain callback duplication issue.
This demonstrates that Sentry creates a duplicate callback when a user
manually configures a SentryLangchainCallback instance.

This script includes both successful and failing requests to show:
1. Duplicate spans in performance monitoring (successful request)
2. Duplicate issues in error tracking (failing request)
"""

import os
import sys
import time

# Add the parent directory to sys.path to use the local sentry_sdk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentry_sdk
from langchain_core.runnables import RunnableConfig
from sentry_sdk.integrations.langchain import (
    LangchainIntegration,
    SentryLangchainCallback,
)

# We'll use a mock LLM for testing without requiring AWS credentials
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from typing import Any, List, Optional, Dict
from langchain_core.callbacks import CallbackManagerForLLMRun


class MockChatModel(BaseChatModel):
    """Mock Chat Model for testing without AWS credentials."""

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Print debugging information about callbacks
        print("\n🔍 Inspecting callbacks in MockChatModel._generate:")
        if run_manager:
            print(f"   Handlers: {len(run_manager.handlers)} total")
            print(
                f"   Inheritable handlers: {len(run_manager.inheritable_handlers)} total"
            )

            # Count SentryLangchainCallback instances
            sentry_callbacks = []
            unique_ids = set()

            for handler in run_manager.handlers:
                if isinstance(handler, SentryLangchainCallback):
                    sentry_callbacks.append(handler)
                    unique_ids.add(id(handler))
                    print(
                        f"   ➜ Found SentryLangchainCallback in handlers (id: {id(handler)})"
                    )

            for handler in run_manager.inheritable_handlers:
                if isinstance(handler, SentryLangchainCallback):
                    if id(handler) not in unique_ids:
                        sentry_callbacks.append(handler)
                        unique_ids.add(id(handler))
                    print(
                        f"   ➜ Found SentryLangchainCallback in inheritable_handlers (id: {id(handler)})"
                    )

            print(
                f"\n📊 Total UNIQUE SentryLangchainCallback instances: {len(unique_ids)}"
            )

            if len(unique_ids) > 1:
                print(
                    "❌ BUG REPRODUCED: Multiple SentryLangchainCallback instances found!"
                )
                print("   This means events will be processed multiple times.")
            else:
                print(
                    "✅ Expected behavior: Only one SentryLangchainCallback instance."
                )

        # Return a simple response
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="Hello!"))],
            llm_output={},
        )

    @property
    def _llm_type(self) -> str:
        return "mock"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Return a dictionary of identifying parameters."""
        return {"model_name": "mock-model"}


class FailingMockChatModel(BaseChatModel):
    """Mock Chat Model that always fails - for testing error duplication."""

    # Store error_type as a class variable to avoid Pydantic field issues
    _error_type: str = "rate_limit"

    def __init__(self, error_type: str = "rate_limit", **kwargs):
        super().__init__(**kwargs)
        # Use object.__setattr__ to bypass Pydantic's field validation
        object.__setattr__(self, "_error_type", error_type)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Print debugging information about callbacks
        print("\n🔍 Inspecting callbacks in FailingMockChatModel._generate:")
        if run_manager:
            print(f"   Handlers: {len(run_manager.handlers)} total")
            print(
                f"   Inheritable handlers: {len(run_manager.inheritable_handlers)} total"
            )

            # Count SentryLangchainCallback instances
            sentry_callbacks = []
            unique_ids = set()

            for handler in run_manager.handlers:
                if isinstance(handler, SentryLangchainCallback):
                    sentry_callbacks.append(handler)
                    unique_ids.add(id(handler))
                    print(
                        f"   ➜ Found SentryLangchainCallback in handlers (id: {id(handler)})"
                    )

            for handler in run_manager.inheritable_handlers:
                if isinstance(handler, SentryLangchainCallback):
                    if id(handler) not in unique_ids:
                        sentry_callbacks.append(handler)
                        unique_ids.add(id(handler))
                    print(
                        f"   ➜ Found SentryLangchainCallback in inheritable_handlers (id: {id(handler)})"
                    )

            print(
                f"\n📊 Total UNIQUE SentryLangchainCallback instances: {len(unique_ids)}"
            )

            if len(unique_ids) > 1:
                print(
                    "❌ BUG REPRODUCED: Multiple SentryLangchainCallback instances found!"
                )
                print("   🚨 This error will be reported to Sentry TWICE!")
            else:
                print(
                    "✅ Expected behavior: Only one SentryLangchainCallback instance."
                )
                print("   ✅ This error would be reported to Sentry once.")

        # Simulate different types of LLM failures
        if self._error_type == "rate_limit":
            raise Exception(
                "OpenAI API rate limit exceeded. Please wait before retrying."
            )
        elif self._error_type == "timeout":
            raise TimeoutError("LLM request timed out after 30 seconds")
        elif self._error_type == "auth":
            raise PermissionError("Invalid API key provided")
        else:
            raise ValueError(f"Unknown error type: {self._error_type}")

    @property
    def _llm_type(self) -> str:
        return "failing-mock"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Return a dictionary of identifying parameters."""
        return {"model_name": "failing-mock-model", "error_type": self._error_type}


def test_successful_request():
    """Test case 1: Successful request (shows duplicate spans)"""
    print("\n" + "=" * 70)
    print("TEST 1: SUCCESSFUL REQUEST (Performance/Spans Duplication)")
    print("=" * 70)

    print("\n1️⃣  Creating a Mock Chat Model...")
    llm = MockChatModel()

    print("\n2️⃣  Creating a SentryLangchainCallback manually...")
    manual_callback = SentryLangchainCallback(
        max_span_map_size=100, include_prompts=True
    )
    print(f"   Manual callback created with id: {id(manual_callback)}")

    print("\n3️⃣  Creating RunnableConfig with the manual callback...")
    config = RunnableConfig(callbacks=[manual_callback])

    print("\n4️⃣  Invoking the LLM with the config...")
    print(
        "   (The integration should detect the existing callback and NOT create a duplicate)"
    )

    try:
        result = llm.invoke("hello", config)
        print(f"\n✅ LLM invocation successful. Result: {result.content}")
        print("\n📊 Expected in Sentry Performance:")
        print("   - You should see duplicate spans for this Langchain operation")
        print("   - Each span will have nearly identical timing")
    except Exception as e:
        print(f"\n❌ Unexpected error in successful test: {e}")


def test_failing_request():
    """Test case 2: Failing request (shows duplicate issues)"""
    print("\n" + "=" * 70)
    print("TEST 2: FAILING REQUEST (Issues/Errors Duplication)")
    print("=" * 70)

    print("\n1️⃣  Creating a Failing Mock Chat Model...")
    failing_llm = FailingMockChatModel(error_type="rate_limit")

    print("\n2️⃣  Creating a SentryLangchainCallback manually...")
    manual_callback = SentryLangchainCallback(
        max_span_map_size=100, include_prompts=True
    )
    print(f"   Manual callback created with id: {id(manual_callback)}")

    print("\n3️⃣  Creating RunnableConfig with the manual callback...")
    config = RunnableConfig(callbacks=[manual_callback])

    print("\n4️⃣  Invoking the FAILING LLM with the config...")
    print("   (This will generate an error that should be reported to Sentry)")

    try:
        result = failing_llm.invoke("hello", config)
        print(f"\n❌ This should not succeed! Result: {result}")
    except Exception as e:
        print(f"\n✅ Expected error occurred: {e}")
        print("\n🚨 Expected in Sentry Issues Dashboard:")
        print("   - You should see TWO identical issues for this error")
        print("   - Both issues will have the same error message and stack trace")
        print("   - This demonstrates the callback duplication bug")

        # Give some time for the error to be sent to Sentry
        print("\n⏳ Waiting 2 seconds for error to be sent to Sentry...")
        time.sleep(2)


def main():
    print("=" * 70)
    print("Langchain Callback Duplication Issue Reproduction")
    print("With Both Performance and Error Tracking Examples")
    print("=" * 70)

    print("\n🔧 Initializing Sentry SDK with Langchain integration...")

    # Use a REAL Sentry DSN if you want to see the issues in your dashboard
    # Replace this with your actual DSN to see the duplication in Sentry UI
    sentry_dsn = os.getenv("SENTRY_DSN", "https://test@sentry.io/123456")

    sentry_sdk.init(
        dsn=sentry_dsn,
        environment="reproduction",
        traces_sample_rate=1.0,
        integrations=[LangchainIntegration()],
        debug=True,
    )

    print(f"   Using DSN: {sentry_dsn}")
    if sentry_dsn.startswith("https://test@"):
        print(
            "   ⚠️  Using dummy DSN - set SENTRY_DSN environment variable to see real issues"
        )
    else:
        print("   ✅ Using real DSN - check your Sentry dashboard for duplicate events")

    # Test successful request (shows span duplication)
    with sentry_sdk.start_transaction(name="test_successful_request"):
        test_successful_request()

    # Test failing request (shows issue duplication)
    with sentry_sdk.start_transaction(name="test_failing_request"):
        test_failing_request()

    print("\n" + "=" * 70)
    print("🏁 Reproduction Complete!")
    print("=" * 70)
    print("\n📋 Summary of what to check in Sentry:")
    print("   1. Performance -> Transactions: Look for duplicate spans")
    print("   2. Issues -> All Issues: Look for duplicate error events")
    print("   3. Both should show 2x the expected events due to callback duplication")

    # Flush any remaining events to Sentry
    print("\n📤 Flushing events to Sentry...")
    sentry_sdk.flush(timeout=5)
    print("✅ Done!")


if __name__ == "__main__":
    main()
