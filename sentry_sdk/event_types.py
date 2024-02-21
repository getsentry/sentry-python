from __future__ import (
    annotations,
)  # dict/list subscripting support in older Python versions
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Literal, Union
    from typing_extensions import TypedDict, Required

    Event = TypedDict(
        "Event",
        {
            "event_id": Required[str],
            "timestamp": Required[Union[str, int, float]],
            "platform": Required[Literal["python"]],
            "level": Literal["fatal", "error", "warning", "info", "debug"],
            "transaction": str,
            "server_name": str,
            "release": str,
            "dist": str,
            "environment": str,
            "modules": dict[str, str],
            "extra": dict[object, object],
            "fingerprint": list[str],
        },
        total=False,
    )
