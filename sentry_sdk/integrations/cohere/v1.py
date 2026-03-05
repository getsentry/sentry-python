import sys
from functools import wraps

from sentry_sdk.ai.span_config import set_request_span_data, set_response_span_data
from sentry_sdk.ai.utils import get_first_from_sources
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.cohere.configs import COHERE_V1_CHAT_CONFIG

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Iterator
    from cohere import StreamedChatResponse

import sentry_sdk
from sentry_sdk.integrations.cohere import (
    CohereIntegration,
    _capture_exception,
)
from sentry_sdk.scope import should_send_default_pii
from sentry_sdk.utils import capture_internal_exceptions, reraise

try:
    from cohere import ChatStreamEndEvent, NonStreamedChatResponse

    try:
        from cohere import StreamEndStreamedChatResponse
    except ImportError:
        from cohere import (
            StreamedChatResponse_StreamEnd as StreamEndStreamedChatResponse,
        )

    _has_chat_types = True
except ImportError:
    _has_chat_types = False


def setup_v1(wrap_embed_fn):
    # type: (Callable[..., Any]) -> None
    try:
        from cohere.base_client import BaseCohere
        from cohere.client import Client
    except ImportError:
        return

    BaseCohere.chat = _wrap_chat(BaseCohere.chat, streaming=False)
    BaseCohere.chat_stream = _wrap_chat(BaseCohere.chat_stream, streaming=True)
    Client.embed = wrap_embed_fn(Client.embed)


def _wrap_chat(f, streaming):
    # type: (Callable[..., Any], bool) -> Callable[..., Any]
    if not _has_chat_types:
        return f

    @wraps(f)
    def new_chat(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        integration = sentry_sdk.get_client().get_integration(CohereIntegration)
        if (
            integration is None
            or "message" not in kwargs
            or not isinstance(kwargs.get("message"), str)
        ):
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
                    span, kwargs, integration, COHERE_V1_CHAT_CONFIG, span_data
                )

                if streaming:
                    return _iter_stream_events(response, span, include_pii)
                set_response_span_data(
                    span, response, include_pii, COHERE_V1_CHAT_CONFIG["response"]
                )
                return response

    return new_chat


def _iter_stream_events(old_iterator, span, include_pii):
    # type: (Any, Any, bool) -> Iterator[StreamedChatResponse]
    for x in old_iterator:
        with capture_internal_exceptions():
            if isinstance(x, ChatStreamEndEvent) or isinstance(
                x, StreamEndStreamedChatResponse
            ):
                response = get_first_from_sources(
                    x, COHERE_V1_CHAT_CONFIG["stream_response_object"]
                )
                if response is not None:
                    set_response_span_data(
                        span, response, include_pii, COHERE_V1_CHAT_CONFIG["response"]
                    )
        yield x
