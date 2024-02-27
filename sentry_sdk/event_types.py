from __future__ import annotations
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Literal, Union
    from typing_extensions import TypedDict, Required

    Event = TypedDict(
        "Event",
        {
            "event_id": Required[str],
            "platform": Required[Literal["python"]],
            "timestamp": Required[Union[str, int, float]],
            "dist": str,
            "environment": str,
            "extra": dict[object, object],
            "fingerprint": list[str],
            "level": Literal["fatal", "error", "warning", "info", "debug"],
            "modules": dict[str, str],
            "release": str,
            "server_name": str,
            "transaction": str,
        },
        total=False,
    )
