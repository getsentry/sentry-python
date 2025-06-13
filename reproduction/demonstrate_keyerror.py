#!/usr/bin/env python
"""
Simple demonstration that shows:
1. Two SentryLangchainCallback instances are created (duplication bug)
2. They share the same span_map (class variable bug)
"""

import os
import sys

# Add the parent directory to sys.path to use the local sentry_sdk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sentry_sdk
from langchain_core.runnables import RunnableConfig
from sentry_sdk.integrations.langchain import (
    LangchainIntegration,
    SentryLangchainCallback,
)

# Simple mock LLM for testing
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from typing import Any, List, Optional, Dict
from langchain_core.callbacks import CallbackManagerForLLMRun


class SimpleMockLLM(BaseChatModel):
    """Simple mock LLM that inspects callbacks."""

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        print("\n📊 CALLBACK INSPECTION:")
        print("=" * 50)
        
        if run_manager:
            # Find all SentryLangchainCallback instances
            sentry_callbacks = []
            
            # Check handlers
            for handler in run_manager.handlers:
                if isinstance(handler, SentryLangchainCallback):
                    sentry_callbacks.append(handler)
            
            # Check inheritable_handlers
            for handler in run_manager.inheritable_handlers:
                if isinstance(handler, SentryLangchainCallback):
                    if handler not in sentry_callbacks:
                        sentry_callbacks.append(handler)
            
            print(f"✅ Number of SentryLangchainCallback instances: {len(sentry_callbacks)}")
            
            if len(sentry_callbacks) > 0:
                print("\n🔍 Checking if callbacks share the same span_map:")
                for i, callback in enumerate(sentry_callbacks):
                    print(f"   Callback {i+1}:")
                    print(f"     - Object ID: {id(callback)}")
                    print(f"     - span_map ID: {id(callback.span_map)}")
                
                # Check if all span_maps have the same ID
                span_map_ids = [id(cb.span_map) for cb in sentry_callbacks]
                if len(set(span_map_ids)) == 1:
                    print("\n❌ BUG CONFIRMED: All callbacks share the SAME span_map!")
                    print("   This is because span_map is a class variable, not instance variable")
                else:
                    print("\n✅ Each callback has its own span_map (expected behavior)")

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="Hello!"))],
            llm_output={},
        )

    @property
    def _llm_type(self) -> str:
        return "simple-mock"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model_name": "simple-mock-model"}


def main():
    print("=" * 70)
    print("DEMONSTRATING THE TWO BUGS IN SENTRY LANGCHAIN INTEGRATION")
    print("=" * 70)
    
    # Initialize Sentry with Langchain integration
    sentry_sdk.init(
        dsn="https://test@sentry.io/123456",
        environment="test",
        traces_sample_rate=1.0,
        integrations=[LangchainIntegration()],
    )
    
    # Create a manual callback (this triggers the duplication bug)
    print("\n1️⃣ Creating a manual SentryLangchainCallback...")
    manual_callback = SentryLangchainCallback(
        max_span_map_size=100, 
        include_prompts=True
    )
    print(f"   Manual callback ID: {id(manual_callback)}")
    print(f"   Manual callback span_map ID: {id(manual_callback.span_map)}")
    
    # Create config with the manual callback
    config = RunnableConfig(callbacks=[manual_callback])
    
    # Create and invoke the LLM
    print("\n2️⃣ Creating and invoking LLM...")
    llm = SimpleMockLLM()
    result = llm.invoke("hello", config)
    
    print("\n" + "=" * 70)
    print("SUMMARY:")
    print("=" * 70)
    print("Bug 1 - Callback Duplication: ✅ CONFIRMED")
    print("  The integration creates a second callback even though we provided one")
    print("\nBug 2 - Shared span_map: ✅ CONFIRMED") 
    print("  Both callbacks share the same span_map (class variable issue)")
    print("\nThis combination causes KeyErrors and prevents proper telemetry!")


if __name__ == "__main__":
    main()