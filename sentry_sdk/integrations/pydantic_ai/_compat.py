"""Version detection and version-dependent imports for pydantic-ai.

Everything here is resolved once at import time. The rest of the integration
consumes the resulting constants instead of probing versions or attributes at
call time, so "what runs on version X" is answered entirely by this module.
"""

from typing import TYPE_CHECKING

from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.utils import package_version

try:
    from pydantic_ai import messages as _messages
    from pydantic_ai.agent import Agent  # noqa: F401
    from pydantic_ai.exceptions import ToolRetryError  # noqa: F401

    try:
        from pydantic_ai.tool_manager import ToolManager
    except ImportError:
        # older versions
        from pydantic_ai._tool_manager import ToolManager  # type: ignore
except ImportError:
    raise DidNotEnable("pydantic-ai not installed")

if TYPE_CHECKING:
    from typing import Optional

# Message part classes are resolved individually so that a single upstream
# rename degrades only the extraction paths that need that class, instead of
# silently disabling all of them at once.
BaseToolCallPart = getattr(_messages, "BaseToolCallPart", None)
BaseToolReturnPart = getattr(_messages, "BaseToolReturnPart", None)
BinaryContent = getattr(_messages, "BinaryContent", None)
ImageUrl = getattr(_messages, "ImageUrl", None)
SystemPromptPart = getattr(_messages, "SystemPromptPart", None)
TextPart = getattr(_messages, "TextPart", None)
ThinkingPart = getattr(_messages, "ThinkingPart", None)

PYDANTIC_AI_VERSION = package_version("pydantic-ai-slim")

# The ToolManager method through which all tool calls flow; renamed from
# _call_tool to execute_tool_call in newer versions. None means the method
# could not be found and tool instrumentation is skipped.
TOOL_CALL_METHOD: "Optional[str]" = None
if hasattr(ToolManager, "execute_tool_call"):
    TOOL_CALL_METHOD = "execute_tool_call"
elif hasattr(ToolManager, "_call_tool"):
    TOOL_CALL_METHOD = "_call_tool"

# Request hooks (pydantic_ai.capabilities) are usable from 1.73 on, when
# ModelRequestContext.model was added:
# https://github.com/pydantic/pydantic-ai/commit/f1260dfe09907f17688eee1646daf898fc428d4c
USES_REQUEST_HOOKS = False
if PYDANTIC_AI_VERSION is not None and PYDANTIC_AI_VERSION >= (1, 73):
    try:
        from pydantic_ai.capabilities import Hooks  # noqa: F401

        USES_REQUEST_HOOKS = True
    except ImportError:
        USES_REQUEST_HOOKS = False

# Which mechanism emits chat spans for model requests: request hooks on new
# versions, graph-node patching on old ones. None (unknown version, or hooks
# unavailable on a new version) means only agent and tool spans are emitted.
MODEL_BACKEND: "Optional[str]" = None
if USES_REQUEST_HOOKS:
    MODEL_BACKEND = "hooks"
elif PYDANTIC_AI_VERSION is not None and PYDANTIC_AI_VERSION < (1, 73):
    MODEL_BACKEND = "graph_nodes"
