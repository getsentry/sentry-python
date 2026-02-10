from typing import TYPE_CHECKING, TypeVar, Union

# Re-exported for compat, since code out there in the wild might use this variable.
MYPY = TYPE_CHECKING


SENSITIVE_DATA_SUBSTITUTE = "[Filtered]"
BLOB_DATA_SUBSTITUTE = "[Blob substitute]"


class AnnotatedValue:
    """
    Meta information for a data field in the event payload.
    This is to tell Relay that we have tampered with the fields value.
    See:
    https://github.com/getsentry/relay/blob/be12cd49a0f06ea932ed9b9f93a655de5d6ad6d1/relay-general/src/types/meta.rs#L407-L423
    """

    __slots__ = ("value", "metadata")

    def __init__(self, value: "Optional[Any]", metadata: "Dict[str, Any]") -> None:
        self.value = value
        self.metadata = metadata

    def __eq__(self, other: "Any") -> bool:
        if not isinstance(other, AnnotatedValue):
            return False

        return self.value == other.value and self.metadata == other.metadata

    def __str__(self: "AnnotatedValue") -> str:
        return str({"value": str(self.value), "metadata": str(self.metadata)})

    def __len__(self: "AnnotatedValue") -> int:
        if self.value is not None:
            return len(self.value)
        else:
            return 0

    @classmethod
    def removed_because_raw_data(cls) -> "AnnotatedValue":
        """The value was removed because it could not be parsed. This is done for request body values that are not json nor a form."""
        return AnnotatedValue(
            value="",
            metadata={
                "rem": [  # Remark
                    [
                        "!raw",  # Unparsable raw data
                        "x",  # The fields original value was removed
                    ]
                ]
            },
        )

    @classmethod
    def removed_because_over_size_limit(cls, value: "Any" = "") -> "AnnotatedValue":
        """
        The actual value was removed because the size of the field exceeded the configured maximum size,
        for example specified with the max_request_body_size sdk option.
        """
        return AnnotatedValue(
            value=value,
            metadata={
                "rem": [  # Remark
                    [
                        "!config",  # Because of configured maximum size
                        "x",  # The fields original value was removed
                    ]
                ]
            },
        )

    @classmethod
    def substituted_because_contains_sensitive_data(cls) -> "AnnotatedValue":
        """The actual value was removed because it contained sensitive information."""
        return AnnotatedValue(
            value=SENSITIVE_DATA_SUBSTITUTE,
            metadata={
                "rem": [  # Remark
                    [
                        "!config",  # Because of SDK configuration (in this case the config is the hard coded removal of certain django cookies)
                        "s",  # The fields original value was substituted
                    ]
                ]
            },
        )


T = TypeVar("T")
Annotated = Union[AnnotatedValue, T]


if TYPE_CHECKING:
    from collections.abc import Container, MutableMapping, Sequence
    from datetime import datetime
    from types import TracebackType
    from typing import Any, Callable, Dict, Mapping, NotRequired, Optional, Type

    from typing_extensions import Literal, TypedDict

    from sentry_sdk.tracing import TransactionSource

    class SDKInfo(TypedDict):
        name: str
        version: str
        packages: "Sequence[Mapping[str, str]]"

    # "critical" is an alias of "fatal" recognized by Relay
    LogLevelStr = Literal["fatal", "critical", "error", "warning", "info", "debug"]

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

    MeasurementValue = TypedDict(
        "MeasurementValue",
        {
            "value": float,
            "unit": NotRequired[Optional[MeasurementUnit]],
        },
    )

    Event = TypedDict(
        "Event",
        {
            "breadcrumbs": Annotated[
                dict[Literal["values"], list[dict[str, Any]]]
            ],  # TODO: We can expand on this type
            "check_in_id": str,
            "contexts": dict[str, dict[str, object]],
            "dist": str,
            "duration": Optional[float],
            "environment": Optional[str],
            "errors": list[dict[str, Any]],  # TODO: We can expand on this type
            "event_id": str,
            "exception": dict[
                Literal["values"], list[dict[str, Any]]
            ],  # TODO: We can expand on this type
            "extra": MutableMapping[str, object],
            "fingerprint": list[str],
            "level": LogLevelStr,
            "logentry": Mapping[str, object],
            "logger": str,
            "measurements": dict[str, MeasurementValue],
            "message": str,
            "modules": dict[str, str],
            "monitor_config": Mapping[str, object],
            "monitor_slug": Optional[str],
            "platform": Literal["python"],
            "profile": object,  # Should be sentry_sdk.profiler.Profile, but we can't import that here due to circular imports
            "release": Optional[str],
            "request": dict[str, object],
            "sdk": Mapping[str, object],
            "server_name": str,
            "spans": Annotated[list[dict[str, object]]],
            "stacktrace": dict[
                str, object
            ],  # We access this key in the code, but I am unsure whether we ever set it
            "start_timestamp": datetime,
            "status": Optional[str],
            "tags": MutableMapping[
                str, str
            ],  # Tags must be less than 200 characters each
            "threads": dict[
                Literal["values"], list[dict[str, Any]]
            ],  # TODO: We can expand on this type
            "timestamp": Optional[datetime],  # Must be set before sending the event
            "transaction": str,
            "transaction_info": Mapping[str, Any],  # TODO: We can expand on this type
            "type": Literal["check_in", "transaction"],
            "user": dict[str, object],
            "_dropped_spans": int,
        },
        total=False,
    )

    ExcInfo = Union[
        tuple[Type[BaseException], BaseException, Optional[TracebackType]],
        tuple[None, None, None],
    ]

    # TODO: Make a proper type definition for this (PRs welcome!)
    Hint = Dict[str, Any]

    AttributeValue = (
        str
        | bool
        | float
        | int
        | list[str]
        | list[bool]
        | list[float]
        | list[int]
        | tuple[str, ...]
        | tuple[bool, ...]
        | tuple[float, ...]
        | tuple[int, ...]
    )
    Attributes = dict[str, AttributeValue]

    SerializedAttributeValue = TypedDict(
        # https://develop.sentry.dev/sdk/telemetry/attributes/#supported-types
        "SerializedAttributeValue",
        {
            "type": Literal[
                "string",
                "boolean",
                "double",
                "integer",
                "array",
            ],
            "value": AttributeValue,
        },
    )

    Log = TypedDict(
        "Log",
        {
            "severity_text": str,
            "severity_number": int,
            "body": str,
            "attributes": Attributes,
            "time_unix_nano": int,
            "trace_id": Optional[str],
            "span_id": Optional[str],
        },
    )

    MetricType = Literal["counter", "gauge", "distribution"]
    MetricUnit = Union[DurationUnit, InformationUnit, str]

    Metric = TypedDict(
        "Metric",
        {
            "timestamp": float,
            "trace_id": Optional[str],
            "span_id": Optional[str],
            "name": str,
            "type": MetricType,
            "value": float,
            "unit": Optional[MetricUnit],
            "attributes": Attributes,
        },
    )

    MetricProcessor = Callable[[Metric, Hint], Optional[Metric]]

    # TODO: Make a proper type definition for this (PRs welcome!)
    Breadcrumb = Dict[str, Any]

    # TODO: Make a proper type definition for this (PRs welcome!)
    BreadcrumbHint = Dict[str, Any]

    _ASGIInfo = TypedDict("_ASGIInfo", {"version": str, "spec_version": str})
    _ASGIScope = TypedDict(
        "_ASGIScope",
        {
            "type": str,
            "asgi": _ASGIInfo,
            "http_version": str,
            "server": tuple[str, int],
            "client": tuple[str, int],
            "scheme": str,
            "method": str,
            "root_path": str,
            "path": str,
            "raw_path": bytes,
            "query_string": bytes,
            "headers": list[tuple[bytes, bytes]],
            "state": dict[str, Any],
        },
    )
    _TransactionContext = TypedDict(
        "_TransactionContext",
        {
            "trace_id": str,
            "span_id": str,
            "parent_span_id": Optional[str],
            "same_process_as_parent": bool,
            "op": Optional[str],
            "description": Optional[str],
            "start_timestamp": datetime | int,
            "timestamp": Optional[datetime],
            "origin": str,
            "tags": NotRequired[dict[str, str]],
            "data": dict[str, Any],
            "name": str,
            "source": TransactionSource,
            "sampled": Optional[bool],
        },
    )
    _SamplingContextTyped = TypedDict(
        "_SamplingContextTyped",
        {
            "transaction_context": _TransactionContext,
            "parent_sampled": Optional[bool],
            "asgi_scope": NotRequired[_ASGIScope],
            # `wsgi_environ` is only present for WSGI server (like Django), and it contains mainly env vars, and some wsgi specifics.
            "wsgi_environ": NotRequired[dict[str, Any]],
            # `aiohttp_request` is only present for AIOHTTP server, and contains <class 'aiohttp.web_request.Request'>
            "aiohttp_request": NotRequired[Any],
            # NOT tested these below, but documented for completeness sake to pass mypy.
            "tornado_request": NotRequired[Any],
            "celery_job": NotRequired[dict[str, Any]],
            "rq_job": NotRequired[Any],
            "aws_event": NotRequired[Any],
            "aws_context": NotRequired[Any],
            "gcp_env": NotRequired[dict[str, Any]],
        },
    )
    SamplingContext = Union[_SamplingContextTyped, dict[str, Any]]

    EventProcessor = Callable[[Event, Hint], Optional[Event]]
    ErrorProcessor = Callable[[Event, ExcInfo], Optional[Event]]
    BreadcrumbProcessor = Callable[[Breadcrumb, BreadcrumbHint], Optional[Breadcrumb]]
    TransactionProcessor = Callable[[Event, Hint], Optional[Event]]
    LogProcessor = Callable[[Log, Hint], Optional[Log]]

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
        "profile_chunk",
        "monitor",
        "span",
        "log_item",
        "log_byte",
        "trace_metric",
    ]
    SessionStatus = Literal["ok", "exited", "crashed", "abnormal"]

    ContinuousProfilerMode = Literal["thread", "gevent", "unknown"]
    ProfilerMode = Union[ContinuousProfilerMode, Literal["sleep"]]

    MonitorConfigScheduleType = Literal["crontab", "interval"]
    MonitorConfigScheduleUnit = Literal[
        "year",
        "month",
        "week",
        "day",
        "hour",
        "minute",
        "second",  # not supported in Sentry and will result in a warning
    ]

    MonitorConfigSchedule = TypedDict(
        "MonitorConfigSchedule",
        {
            "type": MonitorConfigScheduleType,
            "value": Union[int, str],
            "unit": MonitorConfigScheduleUnit,
        },
        total=False,
    )

    MonitorConfig = TypedDict(
        "MonitorConfig",
        {
            "schedule": MonitorConfigSchedule,
            "timezone": str,
            "checkin_margin": int,
            "max_runtime": int,
            "failure_issue_threshold": int,
            "recovery_threshold": int,
        },
        total=False,
    )

    HttpStatusCodeRange = Union[int, Container[int]]

    class TextPart(TypedDict):
        type: Literal["text"]
        content: str
