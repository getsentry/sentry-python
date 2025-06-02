import sentry_sdk
from sentry_sdk.integrations import DidNotEnable, Integration
from sentry_sdk.utils import event_from_exception

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

try:
    from agents import Agent  # noqa: F401
except ImportError:
    raise DidNotEnable("OpenAI Agents not installed")


class OpenAIAgentsIntegration(Integration):
    identifier = "openai_agents"
    origin = f"auto.ai.{identifier}"

    def __init__(self):
        pass

    @staticmethod
    def setup_once():
        # type: () -> None
        pass


def _capture_exception(exc):
    # type: (Any) -> None
    event, hint = event_from_exception(
        exc,
        client_options=sentry_sdk.get_client().options,
        mechanism={"type": OpenAIAgentsIntegration.identifier, "handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)
