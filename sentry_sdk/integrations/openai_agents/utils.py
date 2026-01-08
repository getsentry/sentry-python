import sentry_sdk
from sentry_sdk.ai.utils import (
    GEN_AI_ALLOWED_MESSAGE_ROLES,
    normalize_message_roles,
    parse_data_uri,
    set_data_normalized,
    normalize_message_role,
    truncate_and_annotate_messages,
)
from sentry_sdk.consts import SPANDATA, SPANSTATUS, OP
from sentry_sdk.integrations import DidNotEnable
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.tracing_utils import set_span_errored
from sentry_sdk.utils import event_from_exception, safe_serialize

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from agents import Usage

    from sentry_sdk.tracing import Span

try:
    import agents

except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


def _transform_openai_agents_content_part(
    content_part: "dict[str, Any]",
) -> "dict[str, Any]":
    """
    Transform an OpenAI Agents content part to Sentry-compatible format.

    Handles multimodal content (images, audio, files) by converting them
    to the standardized format:
    - base64 encoded data -> type: "blob"
    - URL references -> type: "uri"
    - file_id references -> type: "file"
    """
    if not isinstance(content_part, dict):
        return content_part

    part_type = content_part.get("type")

    # Handle input_text (OpenAI Agents SDK text format) -> normalize to standard text format
    if part_type == "input_text":
        return {
            "type": "text",
            "text": content_part.get("text", ""),
        }

    # Handle image_url (OpenAI vision format) and input_image (OpenAI Agents SDK format)
    if part_type in ("image_url", "input_image"):
        # Get URL from either format
        if part_type == "image_url":
            image_url = content_part.get("image_url", {})
            url = (
                image_url.get("url", "")
                if isinstance(image_url, dict)
                else str(image_url)
            )
        else:
            # input_image format has image_url directly
            url = content_part.get("image_url", "")

        if url.startswith("data:"):
            try:
                mime_type, content = parse_data_uri(url)
                return {
                    "type": "blob",
                    "modality": "image",
                    "mime_type": mime_type,
                    "content": content,
                }
            except ValueError:
                # If parsing fails, return as URI
                return {
                    "type": "uri",
                    "modality": "image",
                    "mime_type": "",
                    "uri": url,
                }
        else:
            return {
                "type": "uri",
                "modality": "image",
                "mime_type": "",
                "uri": url,
            }

    # Handle input_audio (OpenAI audio input format)
    if part_type == "input_audio":
        input_audio = content_part.get("input_audio", {})
        audio_format = input_audio.get("format", "")
        mime_type = f"audio/{audio_format}" if audio_format else ""
        return {
            "type": "blob",
            "modality": "audio",
            "mime_type": mime_type,
            "content": input_audio.get("data", ""),
        }

    # Handle image_file (Assistants API file-based images)
    if part_type == "image_file":
        image_file = content_part.get("image_file", {})
        return {
            "type": "file",
            "modality": "image",
            "mime_type": "",
            "file_id": image_file.get("file_id", ""),
        }

    # Handle file (document attachments)
    if part_type == "file":
        file_data = content_part.get("file", {})
        return {
            "type": "file",
            "modality": "document",
            "mime_type": "",
            "file_id": file_data.get("file_id", ""),
        }

    return content_part


def _transform_openai_agents_message_content(content: "Any") -> "Any":
    """
    Transform OpenAI Agents message content, handling both string content and
    list of content parts.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, (list, tuple)):
        transformed = []
        for item in content:
            if isinstance(item, dict):
                transformed.append(_transform_openai_agents_content_part(item))
            else:
                transformed.append(item)
        return transformed

    return content


def _capture_exception(exc: "Any") -> None:
    set_span_errored()

    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": "openai_agents", "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


def _record_exception_on_span(span: "Span", error: Exception) -> "Any":
    set_span_errored(span)
    span.set_data("span.status", "error")

    # Optionally capture the error details if we have them
    if hasattr(error, "__class__"):
        span.set_data("error.type", error.__class__.__name__)
    if hasattr(error, "__str__"):
        error_message = str(error)
        if error_message:
            span.set_data("error.message", error_message)


def _set_agent_data(span: "sentry_sdk.tracing.Span", agent: "agents.Agent") -> None:
    span.set_data(
        SPANDATA.GEN_AI_SYSTEM, "openai"
    )  # See footnote for  https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/#gen-ai-system for explanation why.

    span.set_data(SPANDATA.GEN_AI_AGENT_NAME, agent.name)

    if agent.model_settings.max_tokens:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, agent.model_settings.max_tokens
        )

    if agent.model:
        model_name = agent.model.model if hasattr(agent.model, "model") else agent.model
        span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_name)

    if agent.model_settings.presence_penalty:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY,
            agent.model_settings.presence_penalty,
        )

    if agent.model_settings.temperature:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_TEMPERATURE, agent.model_settings.temperature
        )

    if agent.model_settings.top_p:
        span.set_data(SPANDATA.GEN_AI_REQUEST_TOP_P, agent.model_settings.top_p)

    if agent.model_settings.frequency_penalty:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY,
            agent.model_settings.frequency_penalty,
        )

    if len(agent.tools) > 0:
        span.set_data(
            SPANDATA.GEN_AI_REQUEST_AVAILABLE_TOOLS,
            safe_serialize([vars(tool) for tool in agent.tools]),
        )


def _set_usage_data(span: "sentry_sdk.tracing.Span", usage: "Usage") -> None:
    span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS_CACHED,
        usage.input_tokens_details.cached_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)
    span.set_data(
        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS_REASONING,
        usage.output_tokens_details.reasoning_tokens,
    )
    span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, usage.total_tokens)


def _set_input_data(
    span: "sentry_sdk.tracing.Span", get_response_kwargs: "dict[str, Any]"
) -> None:
    if not should_send_default_pii():
        return
    request_messages = []

    system_instructions = get_response_kwargs.get("system_instructions")
    if system_instructions:
        request_messages.append(
            {
                "role": GEN_AI_ALLOWED_MESSAGE_ROLES.SYSTEM,
                "content": [{"type": "text", "text": system_instructions}],
            }
        )

    for message in get_response_kwargs.get("input", []):
        if "role" in message:
            normalized_role = normalize_message_role(message.get("role"))
            content = message.get("content")
            # Transform content to handle multimodal data (images, audio, files)
            transformed_content = _transform_openai_agents_message_content(content)
            request_messages.append(
                {
                    "role": normalized_role,
                    "content": (
                        [{"type": "text", "text": transformed_content}]
                        if isinstance(transformed_content, str)
                        else transformed_content
                    ),
                }
            )
        else:
            if message.get("type") == "function_call":
                request_messages.append(
                    {
                        "role": GEN_AI_ALLOWED_MESSAGE_ROLES.ASSISTANT,
                        "content": [message],
                    }
                )
            elif message.get("type") == "function_call_output":
                request_messages.append(
                    {
                        "role": GEN_AI_ALLOWED_MESSAGE_ROLES.TOOL,
                        "content": [message],
                    }
                )

    normalized_messages = normalize_message_roles(request_messages)
    scope = sentry_sdk.get_current_scope()
    messages_data = truncate_and_annotate_messages(normalized_messages, span, scope)
    if messages_data is not None:
        set_data_normalized(
            span,
            SPANDATA.GEN_AI_REQUEST_MESSAGES,
            messages_data,
            unpack=False,
        )


def _set_output_data(span: "sentry_sdk.tracing.Span", result: "Any") -> None:
    if not should_send_default_pii():
        return

    output_messages: "dict[str, list[Any]]" = {
        "response": [],
        "tool": [],
    }

    for output in result.output:
        if output.type == "function_call":
            output_messages["tool"].append(output.dict())
        elif output.type == "message":
            for output_message in output.content:
                try:
                    output_messages["response"].append(output_message.text)
                except AttributeError:
                    # Unknown output message type, just return the json
                    output_messages["response"].append(output_message.dict())

    if len(output_messages["tool"]) > 0:
        span.set_data(
            SPANDATA.GEN_AI_RESPONSE_TOOL_CALLS, safe_serialize(output_messages["tool"])
        )

    if len(output_messages["response"]) > 0:
        set_data_normalized(
            span, SPANDATA.GEN_AI_RESPONSE_TEXT, output_messages["response"]
        )


def _create_mcp_execute_tool_spans(
    span: "sentry_sdk.tracing.Span", result: "agents.Result"
) -> None:
    for output in result.output:
        if output.__class__.__name__ == "McpCall":
            with sentry_sdk.start_span(
                op=OP.GEN_AI_EXECUTE_TOOL,
                description=f"execute_tool {output.name}",
                start_timestamp=span.start_timestamp,
            ) as execute_tool_span:
                set_data_normalized(execute_tool_span, SPANDATA.GEN_AI_TOOL_TYPE, "mcp")
                set_data_normalized(
                    execute_tool_span, SPANDATA.GEN_AI_TOOL_NAME, output.name
                )
                if should_send_default_pii():
                    execute_tool_span.set_data(
                        SPANDATA.GEN_AI_TOOL_INPUT, output.arguments
                    )
                    execute_tool_span.set_data(
                        SPANDATA.GEN_AI_TOOL_OUTPUT, output.output
                    )
                if output.error:
                    execute_tool_span.set_status(SPANSTATUS.INTERNAL_ERROR)
