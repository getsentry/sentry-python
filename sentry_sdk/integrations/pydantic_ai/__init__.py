from sentry_sdk.integrations import DidNotEnable, Integration

try:
    import pydantic_ai  # noqa: F401
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")


class PydanticAIIntegration(Integration):
    """
    Typical interaction with the library:
    1. The user creates an Agent instance with configuration, including system instructions sent to every model call.
    2. The user calls `Agent.run()` or `Agent.run_stream()` to start an agent run. The latter can be used to incrementally receive progress.
    3. In a loop, the agent repeatedly calls the model, maintaining a conversation history that includes previous messages and tool results, which is passed to each call.

    How the integration is put together:
    - _compat.py resolves the installed pydantic-ai version and every version-dependent decision, once, at import time.
    - _extract.py is the only module that reads pydantic-ai object internals; it returns plain data structures.
    - _spans.py creates spans and writes extracted data onto them.
    - _run_context.py tracks each in-flight run on a contextvar stack; the tool wrapper and span helpers read the current agent from there.
    - _wrap_agent.py instruments Agent.run / Agent.run_stream (invoke_agent spans, isolation scopes, run tracking).
    - _wrap_model.py emits chat spans for model requests via one of two backends chosen in _compat: request hooks (>= 1.73), paired per run through RunContext.metadata, or graph-node patching (older versions).
    - _wrap_tools.py instruments the single ToolManager method all tool calls flow through (execute_tool spans).
    """

    identifier = "pydantic_ai"
    origin = f"auto.ai.{identifier}"

    def __init__(
        self, include_prompts: bool = True, handled_tool_call_exceptions: bool = True
    ) -> None:
        """
        Initialize the Pydantic AI integration.

        Args:
            include_prompts: Whether to include prompts and messages in span data.
                Requires send_default_pii=True. Defaults to True.
            handled_tool_exceptions: Capture tool call exceptions that Pydantic AI
                internally prevents from bubbling up.
        """
        self.include_prompts = include_prompts
        self.handled_tool_call_exceptions = handled_tool_call_exceptions

    @staticmethod
    def setup_once() -> None:
        """
        Set up the pydantic-ai integration.

        This patches the key methods in pydantic-ai to create Sentry spans for:
        - Agent invocations (Agent.run methods)
        - Model requests (AI client calls)
        - Tool executions
        """
        # Deferred imports keep `import sentry_sdk.integrations.pydantic_ai`
        # cheap when the integration is never enabled; they are the only
        # intra-package imports in this module, keeping the import graph
        # acyclic.
        from ._wrap_agent import _patch_agent_run
        from ._wrap_model import install_model_backend
        from ._wrap_tools import _patch_tool_execution

        _patch_agent_run()
        _patch_tool_execution()
        install_model_backend()
