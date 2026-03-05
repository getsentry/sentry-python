import sys
from functools import wraps

from sentry_sdk.ai.span_config import set_request_span_data, set_response_span_data
from sentry_sdk.ai.utils import get_first_from_sources, transitive_getattr
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.cohere.configs import (
    COHERE_V2_CHAT_CONFIG,
    STREAM_DELTA_TEXT_SOURCES,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from sentry_sdk.tracing import Span

import sentry_sdk
from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    _capture_exception,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, reraise

try:
    from cohere.v2.client import V2Client as CohereV2Client

    try:
        from cohere.v2.types import MessageEndV2ChatStreamResponse, V2ChatResponse

        if TYPE_CHECKING:
            from cohere.v2.types import V2ChatStreamResponse
    except ImportError:
        from cohere.types import ChatResponse as V2ChatResponse
        from cohere.types import (
            MessageEndStreamedChatResponseV2 as MessageEndV2ChatStreamResponse,
        )

        if TYPE_CHECKING:
            from cohere.types import StreamedChatResponseV2 as V2ChatStreamResponse

    _has_v2 = True
except ImportError:
    _has_v2 = False


def setup_v2(wrap_embed_fn):
    # type: (Callable[..., Any]) -> None
    if not _has_v2:
        return
    CohereV2Client.chat = _wrap_chat_v2(CohereV2Client.chat, streaming=False)
    CohereV2Client.chat_stream = _wrap_chat_v2(
        CohereV2Client.chat_stream, streaming=True
    )
    CohereV2Client.embed = wrap_embed_fn(CohereV2Client.embed)


def _wrap_chat_v2(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    @wraps(f)
    def new_chat(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if integration is None or "messages" not in kwargs:
            return f(*args, **kwargs)

        model = kwargs.get("model", "")
        include_pii = should_send_default_pii() and integration.include_prompts

        with sentry_sdk.start_span(
            op=OP.GEN_AI_CHAT,
            name=f"chat {model}".strip(),
            origin=CohereIntegration.origin,
        ) as span:
            try:
                response = f(*args, **kwargs)
            except Exception as e:
                exc_info = sys.exc_info()
                with capture_internal_exceptions():
                    _capture_exception(e)
                reraise(*exc_info)

            with capture_internal_exceptions():
                span_data = {
                    SPANDATA.GEN_AI_RESPONSE_STREAMING: streaming,
                    SPANDATA.GEN_AI_REQUEST_MODEL: model if model else None,
                }
                set_request_span_data(
                    span, kwargs, integration, COHERE_V2_CHAT_CONFIG, span_data
                )
                if streaming:
                    return _iter_v2_stream_events(response, span, include_pii)
                set_response_span_data(
                    span, response, include_pii, COHERE_V2_CHAT_CONFIG["response"]
                )
                return response

    return new_chat


def _iter_v2_stream_events(old_iterator, span, include_pii):
    # type: (Any, Span, bool) -> Iterator[V2ChatStreamResponse]
    collected_text = []  # type: list[str]
    for x in old_iterator:
        with capture_internal_exceptions():
            _append_stream_delta_text(collected_text, x)
            if isinstance(x, MessageEndV2ChatStreamResponse):
                set_response_span_data(
                    span,
                    x,
                    include_pii,
                    COHERE_V2_CHAT_CONFIG["stream_response"],
                    collected_text,
                )
        yield x


def _append_stream_delta_text(collected_text, event):
    # type: (list[str], Any) -> None
    if transitive_getattr(event, "type") != "content-delta":
        return
    content_text = get_first_from_sources(event, STREAM_DELTA_TEXT_SOURCES)
    if content_text is not None:
        collected_text.append(content_text)
