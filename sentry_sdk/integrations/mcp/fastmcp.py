"""
Patches for mcp.server.fastmcp.FastMCP class.
"""

import inspect
from functools import wraps
from inspect import Parameter, Signature
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.mcp import MCPIntegration
from sentry_sdk.utils import safe_serialize

if TYPE_CHECKING:
    from typing import Any, Callable

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.utilities.context_injection import find_context_parameter


def _get_span_config(handler_type, handler_name):
    # type: (str, str) -> tuple[str, str, str]
    """
    Get span configuration based on handler type.

    Returns:
        Tuple of (op, span_data_key, span_name)
    """
    if handler_type == "tool":
        op = OP.MCP_TOOL
        span_data_key = SPANDATA.MCP_TOOL_NAME
        mcp_method_name = "tools/call"
    elif handler_type == "prompt":
        op = OP.MCP_PROMPT
        span_data_key = SPANDATA.MCP_PROMPT_NAME
        mcp_method_name = "prompts/get"
    else:  # resource
        op = OP.MCP_RESOURCE
        span_data_key = SPANDATA.MCP_RESOURCE_URI
        mcp_method_name = "resources/read"

    span_name = f"{handler_type} {handler_name}"
    return op, span_data_key, span_name, mcp_method_name


def _set_span_data(span, handler_name, span_data_key, mcp_method_name, kwargs):
    # type: (Any, str, str, str, dict[str, Any]) -> None
    """Set common span data for MCP handlers (legacy, kept for compatibility)."""
    # Extract context_obj from kwargs
    context_obj = None
    for v in kwargs.values():
        if isinstance(v, Context):
            context_obj = v
            break

    _set_span_data_with_context(
        span, handler_name, span_data_key, mcp_method_name, kwargs, context_obj
    )


def _set_span_data_with_context(
    span, handler_name, span_data_key, mcp_method_name, kwargs, context_obj
):
    # type: (Any, str, str, str, dict[str, Any], Any) -> None
    """Set common span data for MCP handlers with explicit context object."""
    # Set handler identifier
    span.set_data(span_data_key, handler_name)
    span.set_data(SPANDATA.MCP_METHOD_NAME, mcp_method_name)

    # Extract request_id from Context if provided
    if context_obj is not None:
        try:
            request_id = context_obj.request_id
            if request_id:
                span.set_data(SPANDATA.MCP_REQUEST_ID, request_id)
        except Exception:
            # Silently ignore if we can't get request_id
            pass

    # Set request arguments (excluding Context objects and internal keys)
    for k, v in kwargs.items():
        if isinstance(v, Context):
            continue
        span.set_data(f"mcp.request.argument.{k}", safe_serialize(v))


def _extract_context_from_kwargs(kwargs):
    # type: (dict[str, Any]) -> Any
    """Extract Context object from kwargs, returns None if not found."""
    for v in kwargs.values():
        if isinstance(v, Context):
            return v
    return None


def _create_span_wrapper(
    original_handler,
    has_context,
    should_inject_context,
    op,
    span_name,
    handler_name,
    span_data_key,
    mcp_method_name,
):
    # type: (Callable[..., Any], bool, bool, str, str, str, str, str) -> Callable[..., Any]
    """
    Creates a wrapper function that handles span creation for MCP handlers.

    This factory function eliminates code duplication by handling all
    combinations of sync/async, with/without context, and with/without injection.

    Strategy Matrix:
    ================

    Case 1: has_context=True
        - Original handler already has a Context parameter
        - Returns: wrapper that extracts Context from kwargs and passes through
        - Context flow: FastMCP → wrapper → original handler
        - Request ID captured from Context

    Case 2: has_context=False, should_inject_context=True
        - Original handler does NOT have a Context parameter
        - Returns: wrapper with injected sentry_ctx parameter
        - Context flow: FastMCP → wrapper (captures ctx) → execute_with_span
        - Original handler called WITHOUT the Context (transparent injection)
        - Request ID captured from injected Context
        - Applies to: Tools without Context

    Case 3: has_context=False, should_inject_context=False
        - Original handler does NOT have a Context parameter
        - No Context injection (to avoid template registration for resources)
        - Returns: simple wrapper with no Context access
        - No request ID capture
        - Applies to: Resources and prompts without Context

    Key Design Points:
    ==================
    - All cases: Use @wraps decorator to preserve function metadata
    - Case 2: Add sentry_ctx: Context to __annotations__ for FastMCP injection
    - Case 2: Modify __signature__ to include sentry_ctx parameter
    - All cases: Use execute_with_span[_async]() for actual span creation (DRY)
    - All cases: Context extraction and span data setting handled uniformly

    Args:
        original_handler: The user's handler function to wrap
        has_context: Whether original_handler already has a Context parameter
        should_inject_context: Whether to inject sentry_ctx parameter
        op: Span operation type (e.g., "mcp.tool")
        span_name: Human-readable span name (e.g., "tool my_tool")
        handler_name: Handler identifier (tool name, prompt name, or resource URI)
        span_data_key: Span data key for handler identifier
        mcp_method_name: MCP protocol method (e.g., "tools/call")

    Returns:
        Wrapped function with span instrumentation and Context handling
    """

    def execute_with_span(context_obj, args, kwargs):
        # type: (Any, tuple[Any, ...], dict[str, Any]) -> Any
        """Execute the handler within a span context."""
        with get_start_span_function()(
            op=op,
            name=span_name,
            origin=MCPIntegration.origin,
        ) as span:
            _set_span_data_with_context(
                span,
                handler_name,
                span_data_key,
                mcp_method_name,
                kwargs,
                context_obj,
            )

            try:
                return original_handler(*args, **kwargs)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise

    async def execute_with_span_async(context_obj, args, kwargs):
        # type: (Any, tuple[Any, ...], dict[str, Any]) -> Any
        """Execute the async handler within a span context."""
        with get_start_span_function()(
            op=op,
            name=span_name,
            origin=MCPIntegration.origin,
        ) as span:
            _set_span_data_with_context(
                span,
                handler_name,
                span_data_key,
                mcp_method_name,
                kwargs,
                context_obj,
            )

            try:
                return await original_handler(*args, **kwargs)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise

    is_async = inspect.iscoroutinefunction(original_handler)

    if has_context:
        # Original has Context - just pass through
        if is_async:

            @wraps(original_handler)
            async def wrapper(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                context_obj = _extract_context_from_kwargs(kwargs)
                return await execute_with_span_async(context_obj, args, kwargs)

            return wrapper
        else:

            @wraps(original_handler)
            def wrapper(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                context_obj = _extract_context_from_kwargs(kwargs)
                return execute_with_span(context_obj, args, kwargs)

            return wrapper
    elif should_inject_context:
        # Original doesn't have Context - need to inject sentry_ctx parameter
        # We need to preserve the original signature and add sentry_ctx

        # Get original signature
        try:
            orig_sig = inspect.signature(original_handler)
        except (ValueError, TypeError):
            # If we can't get signature, fall back to simple wrapper
            orig_sig = None

        if is_async:
            if orig_sig is not None:
                # Create new signature with sentry_ctx parameter added
                new_params = list(orig_sig.parameters.values())
                # Add sentry_ctx as keyword-only parameter with default None
                sentry_ctx_param = Parameter(
                    "sentry_ctx",
                    Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Context,
                )
                new_params.append(sentry_ctx_param)
                new_sig = orig_sig.replace(parameters=new_params)

                # Create wrapper with proper signature
                @wraps(original_handler)
                async def outer_wrapper(*args, **kwargs):
                    # type: (*Any, **Any) -> Any
                    sentry_ctx = kwargs.pop("sentry_ctx", None)
                    return await execute_with_span_async(sentry_ctx, args, kwargs)

                # Set the proper signature
                outer_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
            else:
                # Fallback: use simple wrapper
                @wraps(original_handler)
                async def outer_wrapper(*args, **kwargs):
                    # type: (*Any, **Any) -> Any
                    sentry_ctx = kwargs.pop("sentry_ctx", None)
                    return await execute_with_span_async(sentry_ctx, args, kwargs)

            # Add Context annotation
            outer_wrapper.__annotations__ = getattr(
                original_handler, "__annotations__", {}
            ).copy()
            outer_wrapper.__annotations__["sentry_ctx"] = Context

            return outer_wrapper
        else:
            if orig_sig is not None:
                # Create new signature with sentry_ctx parameter added
                new_params = list(orig_sig.parameters.values())
                # Add sentry_ctx as keyword-only parameter with default None
                sentry_ctx_param = Parameter(
                    "sentry_ctx",
                    Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Context,
                )
                new_params.append(sentry_ctx_param)
                new_sig = orig_sig.replace(parameters=new_params)

                # Create wrapper with proper signature
                @wraps(original_handler)
                def outer_wrapper(*args, **kwargs):
                    # type: (*Any, **Any) -> Any
                    sentry_ctx = kwargs.pop("sentry_ctx", None)
                    return execute_with_span(sentry_ctx, args, kwargs)

                # Set the proper signature
                outer_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
            else:
                # Fallback: use simple wrapper
                @wraps(original_handler)
                def outer_wrapper(*args, **kwargs):
                    # type: (*Any, **Any) -> Any
                    sentry_ctx = kwargs.pop("sentry_ctx", None)
                    return execute_with_span(sentry_ctx, args, kwargs)

            # Add Context annotation
            outer_wrapper.__annotations__ = getattr(
                original_handler, "__annotations__", {}
            ).copy()
            outer_wrapper.__annotations__["sentry_ctx"] = Context

            return outer_wrapper
    else:
        # No context and no injection - simple pass-through wrapper
        if is_async:

            @wraps(original_handler)
            async def wrapper(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                return await execute_with_span_async(None, args, kwargs)

            return wrapper
        else:

            @wraps(original_handler)
            def wrapper(*args, **kwargs):
                # type: (*Any, **Any) -> Any
                return execute_with_span(None, args, kwargs)

            return wrapper


def wrap_handler(original_handler, handler_type, handler_name):
    # type: (Callable[..., Any], str, str) -> Callable[..., Any]
    """
    Wraps a handler function to create spans and capture errors.

    Context injection strategy:
    - Tools without Context: Inject sentry_ctx to capture request_id
    - Resources/prompts without Context: No injection (avoids template registration)
    - Handlers with Context: Use existing Context to capture request_id

    This ensures resources are registered correctly (not as templates) while
    still capturing request_id for tools.

    Args:
        original_handler: The original handler function to wrap
        handler_type: Type of handler ('tool', 'prompt', or 'resource')
        handler_name: Name or identifier of the handler

    Returns:
        Wrapped handler function
    """
    op, span_data_key, span_name, mcp_method_name = _get_span_config(
        handler_type, handler_name
    )

    # Check if the original handler already has a Context parameter
    has_context = find_context_parameter(original_handler) is not None

    # For now, only inject context for tools to avoid resources being registered as templates
    # TODO: Find a better way to capture request_id for resources/prompts without Context
    should_inject_context = handler_type == "tool" and not has_context

    return _create_span_wrapper(
        original_handler,
        has_context,
        should_inject_context,
        op,
        span_name,
        handler_name,
        span_data_key,
        mcp_method_name,
    )


def patch_fastmcp_server():
    # type: () -> None
    """
    Patches the mcp.server.fastmcp.FastMCP class to instrument handler execution.
    """
    from mcp.server.fastmcp import FastMCP

    # Patch tool decorator
    original_tool = FastMCP.tool

    def patched_tool(self, name=None, **kwargs):
        # type: (FastMCP, str | None, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # Use provided name or function name
            tool_name = name if name else getattr(func, "__name__", "unknown")
            wrapped_func = wrap_handler(func, "tool", tool_name)

            # Call the original decorator with the wrapped function
            return original_tool(self, name=name, **kwargs)(wrapped_func)

        return decorator

    FastMCP.tool = patched_tool  # type: ignore

    # Patch prompt decorator
    original_prompt = FastMCP.prompt

    def patched_prompt(self, name=None, **kwargs):
        # type: (FastMCP, str | None, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # Use provided name or function name
            prompt_name = name if name else getattr(func, "__name__", "unknown")
            wrapped_func = wrap_handler(func, "prompt", prompt_name)

            # Call the original decorator with the wrapped function
            return original_prompt(self, name=name, **kwargs)(wrapped_func)

        return decorator

    FastMCP.prompt = patched_prompt  # type: ignore

    # Patch resource decorator
    original_resource = FastMCP.resource

    def patched_resource(self, uri, **kwargs):
        # type: (FastMCP, str, **Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]
        def decorator(func):
            # type: (Callable[..., Any]) -> Callable[..., Any]
            # Use the URI as the identifier
            wrapped_func = wrap_handler(func, "resource", uri)

            # Call the original decorator with the wrapped function
            return original_resource(self, uri, **kwargs)(wrapped_func)

        return decorator

    FastMCP.resource = patched_resource  # type: ignore
