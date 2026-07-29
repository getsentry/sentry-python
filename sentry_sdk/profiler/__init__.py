from sentry_sdk.profiler.continuous_profiler import (
    start_profile_session,
    start_profiler,
    stop_profile_session,
    stop_profiler,
)
from sentry_sdk.profiler.utils import (
    DEFAULT_SAMPLING_FREQUENCY,
    MAX_STACK_DEPTH,
    extract_frame,
    extract_stack,
    frame_id,
    get_frame_name,
)

__all__ = [
    "start_profile_session",  # TODO: Deprecate this in favor of `start_profiler`
    "start_profiler",
    "stop_profile_session",  # TODO: Deprecate this in favor of `stop_profiler`
    "stop_profiler",
    "DEFAULT_SAMPLING_FREQUENCY",
    "MAX_STACK_DEPTH",
    "get_frame_name",
    "extract_frame",
    "extract_stack",
    "frame_id",
]
