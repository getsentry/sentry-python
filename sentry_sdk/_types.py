from __future__ import annotations


try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False


# Re-exported for compat, since code out there in the wild might use this variable.
MYPY = TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime
    from sentry_sdk.profiler import Profile

    from types import TracebackType
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Mapping
    from typing import Optional
    from typing import Tuple
    from typing import Type
    from typing import Union
    from typing_extensions import Literal, TypedDict

    class Event(TypedDict, total=False):
        breadcrumbs: dict[
            Literal["values"], list[dict[str, object]]
        ]  # TODO: We can expand on this type
        contexts: dict[object, object]
        dist: str
        environment: str
        errors: list[dict[str, object]]  # TODO: We can expand on this type
        event_id: str
        exception: dict[
            Literal["values"], list[dict[str, object]]
        ]  # TODO: We can expand on this type
        extra: dict[object, object]
        fingerprint: list[str]
        level: Literal["fatal", "error", "warning", "info", "debug"]
        logger: str
        measurements: dict[str, object]
        modules: dict[str, str]
        platform: Literal["python"]
        profile: Profile
        release: str
        request: dict[str, object]
        server_name: str
        spans: list[dict[str, object]]
        stacktrace: dict[
            str, object
        ]  # We access this key in the code, but I am unsure whether we ever set it
        start_timestamp: Union[datetime, int]
        tags: Union[
            list[str], dict[str, object]
        ]  # Tags must be less than 200 characters each
        threads: dict[
            Literal["values"], list[dict[str, object]]
        ]  # TODO: We can expand on this type
        timestamp: Union[str, int, float]
        transaction: str
        transaction_info: dict[str, object]  # TODO: We can expand on this type
        type: Literal["transaction"]  # Only set this key for transaction events
        user: dict[str, object]
        _metrics_summary: dict[str, object]

    ExcInfo = Tuple[
        Optional[Type[BaseException]], Optional[BaseException], Optional[TracebackType]
    ]

    Hint = Dict[str, Any]

    Breadcrumb = Dict[str, Any]
    BreadcrumbHint = Dict[str, Any]

    SamplingContext = Dict[str, Any]

    EventProcessor = Callable[[Event, Hint], Optional[Event]]
    ErrorProcessor = Callable[[Event, ExcInfo], Optional[Event]]
    BreadcrumbProcessor = Callable[[Breadcrumb, BreadcrumbHint], Optional[Breadcrumb]]
    TransactionProcessor = Callable[[Event, Hint], Optional[Event]]

    TracesSampler = Callable[[SamplingContext], Union[float, int, bool]]

    # https://github.com/python/mypy/issues/5710
    NotImplementedType = Any

    EventDataCategory = Literal[
        "default",
        "error",
        "crash",
        "transaction",
        "security",
        "attachment",
        "session",
        "internal",
        "profile",
        "statsd",
        "monitor",
    ]
    SessionStatus = Literal["ok", "exited", "crashed", "abnormal"]
    EndpointType = Literal["store", "envelope"]

    DurationUnit = Literal[
        "nanosecond",
        "microsecond",
        "millisecond",
        "second",
        "minute",
        "hour",
        "day",
        "week",
    ]

    InformationUnit = Literal[
        "bit",
        "byte",
        "kilobyte",
        "kibibyte",
        "megabyte",
        "mebibyte",
        "gigabyte",
        "gibibyte",
        "terabyte",
        "tebibyte",
        "petabyte",
        "pebibyte",
        "exabyte",
        "exbibyte",
    ]

    FractionUnit = Literal["ratio", "percent"]
    MeasurementUnit = Union[DurationUnit, InformationUnit, FractionUnit, str]

    ProfilerMode = Literal["sleep", "thread", "gevent", "unknown"]

    # Type of the metric.
    MetricType = Literal["d", "s", "g", "c"]

    # Value of the metric.
    MetricValue = Union[int, float, str]

    # Internal representation of tags as a tuple of tuples (this is done in order to allow for the same key to exist
    # multiple times).
    MetricTagsInternal = Tuple[Tuple[str, str], ...]

    # External representation of tags as a dictionary.
    MetricTagValue = Union[
        str,
        int,
        float,
        None,
        List[Union[int, str, float, None]],
        Tuple[Union[int, str, float, None], ...],
    ]
    MetricTags = Mapping[str, MetricTagValue]

    # Value inside the generator for the metric value.
    FlushedMetricValue = Union[int, float]

    BucketKey = Tuple[MetricType, str, MeasurementUnit, MetricTagsInternal]
    MetricMetaKey = Tuple[MetricType, str, MeasurementUnit]
