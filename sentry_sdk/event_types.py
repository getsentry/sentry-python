from __future__ import annotations
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Literal, Union
    from typing_extensions import TypedDict, Required

    class Event(TypedDict, total=False):
        event_id: Required[str]
        platform: Required[Literal["python"]]
        timestamp: Required[Union[str, int, float]]
        dist: str
        environment: str
        errors: list[dict[str, object]]  # TODO: We can expand on this type
        extra: dict[object, object]
        fingerprint: list[str]
        level: Literal["fatal", "error", "warning", "info", "debug"]
        logger: str
        modules: dict[str, str]
        release: str
        server_name: str
        tags: Union[
            list[str], dict[str, object]
        ]  # Tags must be less than 200 characters each
        transaction: str
