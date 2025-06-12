#!/usr/bin/env python
"""
Script to reproduce the Langchain callback duplication issue.
This demonstrates that Sentry creates a duplicate callback when a user
manually configures a SentryLangchainCallback instance.
"""

import os
import sys

# Add the parent directory to sys.path to use the local sentry_sdk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentry_sdk
from langchain_core.runnables import RunnableConfig
from sentry_sdk.integrations.langchain import LangchainIntegration, SentryLangchainCallback

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
        **kwargs: Any
    ) -> ChatResult:
        # Print debugging information about callbacks
        print("\n🔍 Inspecting callbacks in MockChatModel._generate:")
        if run_manager:
            print(f"   Handlers: {len(run_manager.handlers)} total")
            print(f"   Inheritable handlers: {len(run_manager.inheritable_handlers)} total")
            
            # Count SentryLangchainCallback instances
            sentry_callbacks = []
            unique_ids = set()
            
            for handler in run_manager.handlers:
                if isinstance(handler, SentryLangchainCallback):
                    sentry_callbacks.append(handler)
                    unique_ids.add(id(handler))
                    print(f"   ➜ Found SentryLangchainCallback in handlers (id: {id(handler)})")
            
            for handler in run_manager.inheritable_handlers:
                if isinstance(handler, SentryLangchainCallback):
                    if id(handler) not in unique_ids:
                        sentry_callbacks.append(handler)
                        unique_ids.add(id(handler))
                    print(f"   ➜ Found SentryLangchainCallback in inheritable_handlers (id: {id(handler)})")
            
            print(f"\n📊 Total UNIQUE SentryLangchainCallback instances: {len(unique_ids)}")
            
            if len(unique_ids) > 1:
                print("❌ BUG REPRODUCED: Multiple SentryLangchainCallback instances found!")
                print("   This means events will be processed multiple times.")
            else:
                print("✅ Expected behavior: Only one SentryLangchainCallback instance.")
            
        # Return a simple response
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="Hello!"))],
            llm_output={}
        )
    
    @property
    def _llm_type(self) -> str:
        return "mock"
    
    @property 
    def _identifying_params(self) -> Dict[str, Any]:
        """Return a dictionary of identifying parameters."""
        return {"model_name": "mock-model"}


def main():
    print("=" * 60)
    print("Langchain Callback Duplication Issue Reproduction")
    print("=" * 60)
    
    print("\n1️⃣  Initializing Sentry SDK with Langchain integration...")
    sentry_sdk.init(
        dsn="https://test@sentry.io/123456",  # Dummy DSN for testing
        environment="localhost",
        traces_sample_rate=1.0,
        integrations=[LangchainIntegration()],
    )
    
    print("\n2️⃣  Creating a Mock Chat Model...")
    llm = MockChatModel()
    
    print("\n3️⃣  Creating a SentryLangchainCallback manually...")
    manual_callback = SentryLangchainCallback(
        max_span_map_size=100, 
        include_prompts=True
    )
    print(f"   Manual callback created with id: {id(manual_callback)}")
    
    print("\n4️⃣  Creating RunnableConfig with the manual callback...")
    config = RunnableConfig(callbacks=[manual_callback])
    
    print("\n5️⃣  Invoking the LLM with the config...")
    print("   (The integration should detect the existing callback and NOT create a duplicate)")
    
    try:
        result = llm.invoke('hello', config)
        print(f"\n✅ LLM invocation successful. Result: {result.content}")
    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("End of reproduction")
    print("=" * 60)


if __name__ == "__main__":
    main()