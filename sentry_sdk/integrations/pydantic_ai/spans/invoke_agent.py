import sentry_sdk
from sentry_sdk.ai.utils import get_start_span_function, set_data_normalized
from sentry_sdk.consts import OP, SPANDATA

from ..consts import SPAN_ORIGIN
from ..utils import _set_agent_data, _set_model_data, _should_send_prompts

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


def invoke_agent_span(user_prompt, agent, model, model_settings):
    # type: (Any, Any, Any, Any) -> sentry_sdk.tracing.Span
    """Create a span for invoking the agent."""
    start_span_function = get_start_span_function()

    # Determine agent name for span
    name = "agent"
    if agent and hasattr(agent, "name") and agent.name:
        name = agent.name

    span = start_span_function(
        op=OP.GEN_AI_INVOKE_AGENT,
        name=f"invoke_agent {name}",
        origin=SPAN_ORIGIN,
    )
    span.__enter__()

    span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")

    _set_agent_data(span, agent)
    _set_model_data(span, model, model_settings)

    # Add available tools if present
    if agent and hasattr(agent, "_function_toolset"):
        try:
            from sentry_sdk.utils import safe_serialize

            tools = []
            # Get tools from the function toolset
            if hasattr(agent._function_toolset, "tools"):
                for tool_name, tool in agent._function_toolset.tools.items():
                    tool_info = {"name": tool_name}

                    # Add description from function_schema if available
                    if hasattr(tool, "function_schema"):
                        schema = tool.function_schema
                        if hasattr(schema, "description") and schema.description:
                            tool_info["description"] = schema.description

                        # Add parameters from json_schema
                        if hasattr(schema, "json_schema") and schema.json_schema:
                            tool_info["parameters"] = schema.json_schema

                    tools.append(tool_info)

            if tools:
                span.set_data(
                    SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS, safe_serialize(tools)
                )
        except Exception:
            # If we can't extract tools, just skip it
            pass

    # Add user prompt and system prompts if available and prompts are enabled
    if _should_send_prompts():
        messages = []

        # Add system prompts (both instructions and system_prompt)
        system_texts = []

        if agent:
            # Check for system_prompt
            if hasattr(agent, "_system_prompts") and agent._system_prompts:
                for prompt in agent._system_prompts:
                    if isinstance(prompt, str):
                        system_texts.append(prompt)

            # Check for instructions (stored in _instructions)
            if hasattr(agent, "_instructions") and agent._instructions:
                instructions = agent._instructions
                if isinstance(instructions, str):
                    system_texts.append(instructions)
                elif isinstance(instructions, (list, tuple)):
                    for instr in instructions:
                        if isinstance(instr, str):
                            system_texts.append(instr)
                        elif callable(instr):
                            # Skip dynamic/callable instructions
                            pass

        # Add all system texts as system messages
        for system_text in system_texts:
            messages.append(
                {
                    "content": [{"text": system_text, "type": "text"}],
                    "role": "system",
                }
            )

        # Add user prompt
        if user_prompt:
            if isinstance(user_prompt, str):
                messages.append(
                    {
                        "content": [{"text": user_prompt, "type": "text"}],
                        "role": "user",
                    }
                )
            elif isinstance(user_prompt, list):
                # Handle list of user content
                content = []
                for item in user_prompt:
                    if isinstance(item, str):
                        content.append({"text": item, "type": "text"})
                if content:
                    messages.append(
                        {
                            "content": content,
                            "role": "user",
                        }
                    )

        if messages:
            set_data_normalized(
                span, SPANDATA.GEN_AI_REQUEST_MESSAGES, messages, unpack=False
            )

    return span


def update_invoke_agent_span(span, output):
    # type: (sentry_sdk.tracing.Span, Any) -> None
    """Update and close the invoke agent span."""
    if span and _should_send_prompts() and output:
        output_text = str(output) if not isinstance(output, str) else output
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, output_text, unpack=False
        )

    if span:
        span.__exit__(None, None, None)
