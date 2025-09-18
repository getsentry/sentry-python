import itertools
from enum import Enum
from typing import TYPE_CHECKING

from sentry_conventions.attributes import ATTRIBUTE_NAMES as ATTRS  # noqa: F401

# up top to prevent circular import due to integration import
# This is more or less an arbitrary large-ish value for now, so that we allow
# pretty long strings (like LLM prompts), but still have *some* upper limit
# until we verify that removing the trimming completely is safe.
DEFAULT_MAX_VALUE_LENGTH = 100_000

DEFAULT_MAX_STACK_FRAMES = 100
DEFAULT_ADD_FULL_STACK = False


# Also needs to be at the top to prevent circular import
class EndpointType(Enum):
    """
    The type of an endpoint. This is an enum, rather than a constant, for historical reasons
    (the old /store endpoint). The enum also preserve future compatibility, in case we ever
    have a new endpoint.
    """

    ENVELOPE = "envelope"


class CompressionAlgo(Enum):
    GZIP = "gzip"
    BROTLI = "br"


if TYPE_CHECKING:
    import sentry_sdk

    from typing import Optional
    from typing import Callable
    from typing import Union
    from typing import List
    from typing import Type
    from typing import Dict
    from typing import Any
    from typing import Sequence
    from typing import Tuple
    from typing_extensions import Literal
    from typing_extensions import TypedDict

    from sentry_sdk._types import (
        BreadcrumbProcessor,
        ContinuousProfilerMode,
        Event,
        EventProcessor,
        Hint,
        Log,
        MeasurementUnit,
        ProfilerMode,
        TracesSampler,
        TransactionProcessor,
        MetricTags,
        MetricValue,
    )

    # Experiments are feature flags to enable and disable certain unstable SDK
    # functionality. Changing them from the defaults (`None`) in production
    # code is highly discouraged. They are not subject to any stability
    # guarantees such as the ones from semantic versioning.
    Experiments = TypedDict(
        "Experiments",
        {
            "max_spans": Optional[int],
            "max_flags": Optional[int],
            "record_sql_params": Optional[bool],
            "continuous_profiling_auto_start": Optional[bool],
            "continuous_profiling_mode": Optional[ContinuousProfilerMode],
            "otel_powered_performance": Optional[bool],
            "transport_zlib_compression_level": Optional[int],
            "transport_compression_level": Optional[int],
            "transport_compression_algo": Optional[CompressionAlgo],
            "transport_num_pools": Optional[int],
            "transport_http2": Optional[bool],
            "enable_metrics": Optional[bool],
            "before_emit_metric": Optional[
                Callable[[str, MetricValue, MeasurementUnit, MetricTags], bool]
            ],
            "metric_code_locations": Optional[bool],
            "enable_logs": Optional[bool],
            "before_send_log": Optional[Callable[[Log, Hint], Optional[Log]]],
        },
        total=False,
    )

DEFAULT_QUEUE_SIZE = 100
DEFAULT_MAX_BREADCRUMBS = 100
MATCH_ALL = r".*"

FALSE_VALUES = [
    "false",
    "no",
    "off",
    "n",
    "0",
]


class SPANTEMPLATE(str, Enum):
    DEFAULT = "default"
    AI_AGENT = "ai_agent"
    AI_TOOL = "ai_tool"
    AI_CHAT = "ai_chat"

    def __str__(self):
        # type: () -> str
        return self.value


class INSTRUMENTER:
    SENTRY = "sentry"
    OTEL = "otel"


class SPANSTATUS:
    """
    The status of a Sentry span.

    See: https://develop.sentry.dev/sdk/event-payloads/contexts/#trace-context
    """

    ABORTED = "aborted"
    ALREADY_EXISTS = "already_exists"
    CANCELLED = "cancelled"
    DATA_LOSS = "data_loss"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    FAILED_PRECONDITION = "failed_precondition"
    INTERNAL_ERROR = "internal_error"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    OK = "ok"
    OUT_OF_RANGE = "out_of_range"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"
    UNIMPLEMENTED = "unimplemented"
    UNKNOWN_ERROR = "unknown_error"


class OP:
    ANTHROPIC_MESSAGES_CREATE = "ai.messages.create.anthropic"
    CACHE_GET = "cache.get"
    CACHE_PUT = "cache.put"
    COHERE_CHAT_COMPLETIONS_CREATE = "ai.chat_completions.create.cohere"
    COHERE_EMBEDDINGS_CREATE = "ai.embeddings.create.cohere"
    DB = "db"
    DB_REDIS = "db.redis"
    EVENT_DJANGO = "event.django"
    FUNCTION = "function"
    FUNCTION_AWS = "function.aws"
    FUNCTION_GCP = "function.gcp"
    GEN_AI_CHAT = "gen_ai.chat"
    GEN_AI_CREATE_AGENT = "gen_ai.create_agent"
    GEN_AI_EMBEDDINGS = "gen_ai.embeddings"
    GEN_AI_EXECUTE_TOOL = "gen_ai.execute_tool"
    GEN_AI_GENERATE_TEXT = "gen_ai.generate_text"
    GEN_AI_HANDOFF = "gen_ai.handoff"
    GEN_AI_PIPELINE = "gen_ai.pipeline"
    GEN_AI_INVOKE_AGENT = "gen_ai.invoke_agent"
    GEN_AI_RESPONSES = "gen_ai.responses"
    GRAPHQL_EXECUTE = "graphql.execute"
    GRAPHQL_MUTATION = "graphql.mutation"
    GRAPHQL_PARSE = "graphql.parse"
    GRAPHQL_RESOLVE = "graphql.resolve"
    GRAPHQL_SUBSCRIPTION = "graphql.subscription"
    GRAPHQL_QUERY = "graphql.query"
    GRAPHQL_VALIDATE = "graphql.validate"
    GRPC_CLIENT = "grpc.client"
    GRPC_SERVER = "grpc.server"
    HTTP_CLIENT = "http.client"
    HTTP_CLIENT_STREAM = "http.client.stream"
    HTTP_SERVER = "http.server"
    MIDDLEWARE_DJANGO = "middleware.django"
    MIDDLEWARE_LITESTAR = "middleware.litestar"
    MIDDLEWARE_LITESTAR_RECEIVE = "middleware.litestar.receive"
    MIDDLEWARE_LITESTAR_SEND = "middleware.litestar.send"
    MIDDLEWARE_STARLETTE = "middleware.starlette"
    MIDDLEWARE_STARLETTE_RECEIVE = "middleware.starlette.receive"
    MIDDLEWARE_STARLETTE_SEND = "middleware.starlette.send"
    MIDDLEWARE_STARLITE = "middleware.starlite"
    MIDDLEWARE_STARLITE_RECEIVE = "middleware.starlite.receive"
    MIDDLEWARE_STARLITE_SEND = "middleware.starlite.send"
    HUGGINGFACE_HUB_CHAT_COMPLETIONS_CREATE = (
        "ai.chat_completions.create.huggingface_hub"
    )
    QUEUE_PROCESS = "queue.process"
    QUEUE_PUBLISH = "queue.publish"
    QUEUE_SUBMIT_ARQ = "queue.submit.arq"
    QUEUE_TASK_ARQ = "queue.task.arq"
    QUEUE_SUBMIT_CELERY = "queue.submit.celery"
    QUEUE_TASK_CELERY = "queue.task.celery"
    QUEUE_TASK_RQ = "queue.task.rq"
    QUEUE_SUBMIT_HUEY = "queue.submit.huey"
    QUEUE_TASK_HUEY = "queue.task.huey"
    QUEUE_SUBMIT_RAY = "queue.submit.ray"
    QUEUE_TASK_RAY = "queue.task.ray"
    SUBPROCESS = "subprocess"
    SUBPROCESS_WAIT = "subprocess.wait"
    SUBPROCESS_COMMUNICATE = "subprocess.communicate"
    TEMPLATE_RENDER = "template.render"
    VIEW_RENDER = "view.render"
    VIEW_RESPONSE_RENDER = "view.response.render"
    WEBSOCKET_SERVER = "websocket.server"
    SOCKET_CONNECTION = "socket.connection"
    SOCKET_DNS = "socket.dns"


# This type exists to trick mypy and PyCharm into thinking `init` and `Client`
# take these arguments (even though they take opaque **kwargs)
class ClientConstructor:

    def __init__(
        self,
        dsn=None,  # type: Optional[str]
        *,
        max_breadcrumbs=DEFAULT_MAX_BREADCRUMBS,  # type: int
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        server_name=None,  # type: Optional[str]
        shutdown_timeout=2,  # type: float
        integrations=[],  # type: Sequence[sentry_sdk.integrations.Integration]  # noqa: B006
        in_app_include=[],  # type: List[str]  # noqa: B006
        in_app_exclude=[],  # type: List[str]  # noqa: B006
        default_integrations=True,  # type: bool
        dist=None,  # type: Optional[str]
        transport=None,  # type: Optional[Union[sentry_sdk.transport.Transport, Type[sentry_sdk.transport.Transport], Callable[[Event], None]]]
        transport_queue_size=DEFAULT_QUEUE_SIZE,  # type: int
        sample_rate=1.0,  # type: float
        send_default_pii=None,  # type: Optional[bool]
        http_proxy=None,  # type: Optional[str]
        https_proxy=None,  # type: Optional[str]
        ignore_errors=[],  # type: Sequence[Union[type, str]]  # noqa: B006
        max_request_body_size="medium",  # type: str
        socket_options=None,  # type: Optional[List[Tuple[int, int, int | bytes]]]
        keep_alive=None,  # type: Optional[bool]
        before_send=None,  # type: Optional[EventProcessor]
        before_breadcrumb=None,  # type: Optional[BreadcrumbProcessor]
        debug=None,  # type: Optional[bool]
        attach_stacktrace=False,  # type: bool
        ca_certs=None,  # type: Optional[str]
        propagate_traces=True,  # type: bool
        traces_sample_rate=None,  # type: Optional[float]
        traces_sampler=None,  # type: Optional[TracesSampler]
        profiles_sample_rate=None,  # type: Optional[float]
        profiles_sampler=None,  # type: Optional[TracesSampler]
        profiler_mode=None,  # type: Optional[ProfilerMode]
        profile_lifecycle="manual",  # type: Literal["manual", "trace"]
        profile_session_sample_rate=None,  # type: Optional[float]
        auto_enabling_integrations=True,  # type: bool
        disabled_integrations=None,  # type: Optional[Sequence[sentry_sdk.integrations.Integration]]
        auto_session_tracking=True,  # type: bool
        send_client_reports=True,  # type: bool
        _experiments={},  # type: Experiments  # noqa: B006
        proxy_headers=None,  # type: Optional[Dict[str, str]]
        instrumenter=INSTRUMENTER.SENTRY,  # type: Optional[str]
        before_send_transaction=None,  # type: Optional[TransactionProcessor]
        project_root=None,  # type: Optional[str]
        enable_tracing=None,  # type: Optional[bool]
        include_local_variables=True,  # type: Optional[bool]
        include_source_context=True,  # type: Optional[bool]
        trace_propagation_targets=[  # noqa: B006
            MATCH_ALL
        ],  # type: Optional[Sequence[str]]
        functions_to_trace=[],  # type: Sequence[Dict[str, str]]  # noqa: B006
        event_scrubber=None,  # type: Optional[sentry_sdk.scrubber.EventScrubber]
        max_value_length=DEFAULT_MAX_VALUE_LENGTH,  # type: int
        enable_backpressure_handling=True,  # type: bool
        error_sampler=None,  # type: Optional[Callable[[Event, Hint], Union[float, bool]]]
        enable_db_query_source=True,  # type: bool
        db_query_source_threshold_ms=100,  # type: int
        spotlight=None,  # type: Optional[Union[bool, str]]
        cert_file=None,  # type: Optional[str]
        key_file=None,  # type: Optional[str]
        custom_repr=None,  # type: Optional[Callable[..., Optional[str]]]
        add_full_stack=DEFAULT_ADD_FULL_STACK,  # type: bool
        max_stack_frames=DEFAULT_MAX_STACK_FRAMES,  # type: Optional[int]
        enable_logs=False,  # type: bool
        before_send_log=None,  # type: Optional[Callable[[Log, Hint], Optional[Log]]]
    ):
        # type: (...) -> None
        """Initialize the Sentry SDK with the given parameters. All parameters described here can be used in a call to `sentry_sdk.init()`.

        :param dsn: The DSN tells the SDK where to send the events.

            If this option is not set, the SDK will just not send any data.

            The `dsn` config option takes precedence over the environment variable.

            Learn more about `DSN utilization <https://docs.sentry.io/product/sentry-basics/dsn-explainer/#dsn-utilization>`_.

        :param debug: Turns debug mode on or off.

            When `True`, the SDK will attempt to print out debugging information. This can be useful if something goes
            wrong with event sending.

            The default is always `False`. It's generally not recommended to turn it on in production because of the
            increase in log output.

            The `debug` config option takes precedence over the environment variable.

        :param release: Sets the release.

            If not set, the SDK will try to automatically configure a release out of the box but it's a better idea to
            manually set it to guarantee that the release is in sync with your deploy integrations.

            Release names are strings, but some formats are detected by Sentry and might be rendered differently.

            See `the releases documentation <https://docs.sentry.io/platforms/python/configuration/releases/>`_ to learn how the SDK tries to
            automatically configure a release.

            The `release` config option takes precedence over the environment variable.

            Learn more about how to send release data so Sentry can tell you about regressions between releases and
            identify the potential source in `the product documentation <https://docs.sentry.io/product/releases/>`_.

        :param environment: Sets the environment. This string is freeform and set to `production` by default.

            A release can be associated with more than one environment to separate them in the UI (think `staging` vs
            `production` or similar).

            The `environment` config option takes precedence over the environment variable.

        :param dist: The distribution of the application.

            Distributions are used to disambiguate build or deployment variants of the same release of an application.

            The dist can be for example a build number.

        :param sample_rate: Configures the sample rate for error events, in the range of `0.0` to `1.0`.

            The default is `1.0`, which means that 100% of error events will be sent. If set to `0.1`, only 10% of
            error events will be sent.

            Events are picked randomly.

        :param error_sampler: Dynamically configures the sample rate for error events on a per-event basis.

            This configuration option accepts a function, which takes two parameters (the `event` and the `hint`), and
            which returns a boolean (indicating whether the event should be sent to Sentry) or a floating-point number
            between `0.0` and `1.0`, inclusive.

            The number indicates the probability the event is sent to Sentry; the SDK will randomly decide whether to
            send the event with the given probability.

            If this configuration option is specified, the `sample_rate` option is ignored.

        :param ignore_errors: A list of exception class names that shouldn't be sent to Sentry.

            Errors that are an instance of these exceptions or a subclass of them, will be filtered out before they're
            sent to Sentry.

            By default, all errors are sent.

        :param max_breadcrumbs: This variable controls the total amount of breadcrumbs that should be captured.

            This defaults to `100`, but you can set this to any number.

            However, you should be aware that Sentry has a `maximum payload size <https://develop.sentry.dev/sdk/data-model/envelopes/#size-limits>`_
            and any events exceeding that payload size will be dropped.

        :param attach_stacktrace: When enabled, stack traces are automatically attached to all messages logged.

            Stack traces are always attached to exceptions; however, when this option is set, stack traces are also
            sent with messages.

            This option means that stack traces appear next to all log messages.

            Grouping in Sentry is different for events with stack traces and without. As a result, you will get new
            groups as you enable or disable this flag for certain events.

        :param send_default_pii: If this flag is enabled, `certain personally identifiable information (PII)
            <https://docs.sentry.io/platforms/python/data-management/data-collected/>`_ is added by active integrations.

            If you enable this option, be sure to manually remove what you don't want to send using our features for
            managing `Sensitive Data <https://docs.sentry.io/data-management/sensitive-data/>`_.

        :param event_scrubber: Scrubs the event payload for sensitive information such as cookies, sessions, and
            passwords from a `denylist`.

            It can additionally be used to scrub from another `pii_denylist` if `send_default_pii` is disabled.

            See how to `configure the scrubber here <https://docs.sentry.io/data-management/sensitive-data/#event-scrubber>`_.

        :param include_source_context: When enabled, source context will be included in events sent to Sentry.

            This source context includes the five lines of code above and below the line of code where an error
            happened.

        :param include_local_variables: When enabled, the SDK will capture a snapshot of local variables to send with
            the event to help with debugging.

        :param add_full_stack: When capturing errors, Sentry stack traces typically only include frames that start the
            moment an error occurs.

            But if the `add_full_stack` option is enabled (set to `True`), all frames from the start of execution will
            be included in the stack trace sent to Sentry.

        :param max_stack_frames: This option limits the number of stack frames that will be captured when
            `add_full_stack` is enabled.

        :param server_name: This option can be used to supply a server name.

            When provided, the name of the server is sent along and persisted in the event.

            For many integrations, the server name actually corresponds to the device hostname, even in situations
            where the machine is not actually a server.

        :param project_root: The full path to the root directory of your application.

            The `project_root` is used to mark frames in a stack trace either as being in your application or outside
            of the application.

        :param in_app_include: A list of string prefixes of module names that belong to the app.

            This option takes precedence over `in_app_exclude`.

            Sentry differentiates stack frames that are directly related to your application ("in application") from
            stack frames that come from other packages such as the standard library, frameworks, or other dependencies.

            The application package is automatically marked as `inApp`.

            The difference is visible in [sentry.io](https://sentry.io), where only the "in application" frames are
            displayed by default.

        :param in_app_exclude: A list of string prefixes of module names that do not belong to the app, but rather to
            third-party packages.

            Modules considered not part of the app will be hidden from stack traces by default.

            This option can be overridden using `in_app_include`.

        :param max_request_body_size: This parameter controls whether integrations should capture HTTP request bodies.
            It can be set to one of the following values:

            - `never`: Request bodies are never sent.
            - `small`: Only small request bodies will be captured. The cutoff for small depends on the SDK (typically
              4KB).
            - `medium`: Medium and small requests will be captured (typically 10KB).
            - `always`: The SDK will always capture the request body as long as Sentry can make sense of it.

            Please note that the Sentry server [limits HTTP request body size](https://develop.sentry.dev/sdk/
            expected-features/data-handling/#variable-size). The server always enforces its size limit, regardless of
            how you configure this option.

        :param max_value_length: The number of characters after which the values containing text in the event payload
            will be truncated.

            WARNING: If the value you set for this is exceptionally large, the event may exceed 1 MiB and will be
            dropped by Sentry.

        :param ca_certs: A path to an alternative CA bundle file in PEM-format.

        :param send_client_reports: Set this boolean to `False` to disable sending of client reports.

            Client reports allow the client to send status reports about itself to Sentry, such as information about
            events that were dropped before being sent.

        :param integrations: List of integrations to enable in addition to `auto-enabling integrations (overview)
            <https://docs.sentry.io/platforms/python/integrations>`_.

            This setting can be used to override the default config options for a specific auto-enabling integration
            or to add an integration that is not auto-enabled.

        :param disabled_integrations: List of integrations that will be disabled.

            This setting can be used to explicitly turn off specific `auto-enabling integrations (list)
            <https://docs.sentry.io/platforms/python/integrations/#available-integrations>`_ or
            `default <https://docs.sentry.io/platforms/python/integrations/default-integrations/>`_ integrations.

        :param auto_enabling_integrations: Configures whether `auto-enabling integrations (configuration)
            <https://docs.sentry.io/platforms/python/integrations/#available-integrations>`_ should be enabled.

            When set to `False`, no auto-enabling integrations will be enabled by default, even if the corresponding
            framework/library is detected.

        :param default_integrations: Configures whether `default integrations
            <https://docs.sentry.io/platforms/python/integrations/default-integrations/>`_ should be enabled.

            Setting `default_integrations` to `False` disables all default integrations **as well as all auto-enabling
            integrations**, unless they are specifically added in the `integrations` option, described above.

        :param before_send: This function is called with an SDK-specific message or error event object, and can return
            a modified event object, or `null` to skip reporting the event.

            This can be used, for instance, for manual PII stripping before sending.

            By the time `before_send` is executed, all scope data has already been applied to the event. Further
            modification of the scope won't have any effect.

        :param before_send_transaction: This function is called with an SDK-specific transaction event object, and can
            return a modified transaction event object, or `null` to skip reporting the event.

            One way this might be used is for manual PII stripping before sending.

        :param before_breadcrumb: This function is called with an SDK-specific breadcrumb object before the breadcrumb
            is added to the scope.

            When nothing is returned from the function, the breadcrumb is dropped.

            To pass the breadcrumb through, return the first argument, which contains the breadcrumb object.

            The callback typically gets a second argument (called a "hint") which contains the original object from
            which the breadcrumb was created to further customize what the breadcrumb should look like.

        :param transport: Switches out the transport used to send events.

            How this works depends on the SDK. It can, for instance, be used to capture events for unit-testing or to
            send it through some more complex setup that requires proxy authentication.

        :param transport_queue_size: The maximum number of events that will be queued before the transport is forced to
            flush.

        :param http_proxy: When set, a proxy can be configured that should be used for outbound requests.

            This is also used for HTTPS requests unless a separate `https_proxy` is configured. However, not all SDKs
            support a separate HTTPS proxy.

            SDKs will attempt to default to the system-wide configured proxy, if possible. For instance, on Unix
            systems, the `http_proxy` environment variable will be picked up.

        :param https_proxy: Configures a separate proxy for outgoing HTTPS requests.

            This value might not be supported by all SDKs. When not supported the `http-proxy` value is also used for
            HTTPS requests at all times.

        :param proxy_headers: A dict containing additional proxy headers (usually for authentication) to be forwarded
            to `urllib3`'s `ProxyManager <https://urllib3.readthedocs.io/en/1.24.3/reference/index.html#urllib3.poolmanager.ProxyManager>`_.

        :param shutdown_timeout: Controls how many seconds to wait before shutting down.

            Sentry SDKs send events from a background queue. This queue is given a certain amount to drain pending
            events. The default is SDK specific but typically around two seconds.

            Setting this value too low may cause problems for sending events from command line applications.

            Setting the value too high will cause the application to block for a long time for users experiencing
            network connectivity problems.

        :param keep_alive: Determines whether to keep the connection alive between requests.

            This can be useful in environments where you encounter frequent network issues such as connection resets.

        :param cert_file: Path to the client certificate to use.

            If set, supersedes the `CLIENT_CERT_FILE` environment variable.

        :param key_file: Path to the key file to use.

            If set, supersedes the `CLIENT_KEY_FILE` environment variable.

        :param socket_options: An optional list of socket options to use.

            These provide fine-grained, low-level control over the way the SDK connects to Sentry.

            If provided, the options will override the default `urllib3` `socket options
            <https://urllib3.readthedocs.io/en/stable/reference/urllib3.connection.html#urllib3.connection.HTTPConnection>`_.

        :param traces_sample_rate: A number between `0` and `1`, controlling the percentage chance a given transaction
            will be sent to Sentry.

            (`0` represents 0% while `1` represents 100%.) Applies equally to all transactions created in the app.

            Either this or `traces_sampler` must be defined to enable tracing.

            If `traces_sample_rate` is `0`, this means that no new traces will be created. However, if you have
            another service (for example a JS frontend) that makes requests to your service that include trace
            information, those traces will be continued and thus transactions will be sent to Sentry.

            If you want to disable all tracing you need to set `traces_sample_rate=None`. In this case, no new traces
            will be started and no incoming traces will be continued.

        :param traces_sampler: A function responsible for determining the percentage chance a given transaction will be
            sent to Sentry.

            It will automatically be passed information about the transaction and the context in which it's being
            created, and must return a number between `0` (0% chance of being sent) and `1` (100% chance of being
            sent).

            Can also be used for filtering transactions, by returning `0` for those that are unwanted.

            Either this or `traces_sample_rate` must be defined to enable tracing.

        :param trace_propagation_targets: An optional property that controls which downstream services receive tracing
            data, in the form of a `sentry-trace` and a `baggage` header attached to any outgoing HTTP requests.

            The option may contain a list of strings or regex against which the URLs of outgoing requests are matched.

            If one of the entries in the list matches the URL of an outgoing request, trace data will be attached to
            that request.

            String entries do not have to be full matches, meaning the URL of a request is matched when it _contains_
            a string provided through the option.

            If `trace_propagation_targets` is not provided, trace data is attached to every outgoing request from the
            instrumented client.

        :param functions_to_trace: An optional list of functions that should be set up for tracing.

            For each function in the list, a span will be created when the function is executed.

            Functions in the list are represented as strings containing the fully qualified name of the function.

            This is a convenient option, making it possible to have one central place for configuring what functions
            to trace, instead of having custom instrumentation scattered all over your code base.

            To learn more, see the `Custom Instrumentation <https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/#define-span-creation-in-a-central-place>`_ documentation.

        :param enable_backpressure_handling: When enabled, a new monitor thread will be spawned to perform health
            checks on the SDK.

            If the system is unhealthy, the SDK will keep halving the `traces_sample_rate` set by you in 10 second
            intervals until recovery.

            This down sampling helps ensure that the system stays stable and reduces SDK overhead under high load.

            This option is enabled by default.

        :param enable_db_query_source: When enabled, the source location will be added to database queries.

        :param db_query_source_threshold_ms: The threshold in milliseconds for adding the source location to database
            queries.

            The query location will be added to the query for queries slower than the specified threshold.

        :param custom_repr: A custom `repr <https://docs.python.org/3/library/functions.html#repr>`_ function to run
            while serializing an object.

            Use this to control how your custom objects and classes are visible in Sentry.

            Return a string for that repr value to be used or `None` to continue serializing how Sentry would have
            done it anyway.

        :param profiles_sample_rate: A number between `0` and `1`, controlling the percentage chance a given sampled
            transaction will be profiled.

            (`0` represents 0% while `1` represents 100%.) Applies equally to all transactions created in the app.

            This is relative to the tracing sample rate - e.g. `0.5` means 50% of sampled transactions will be
            profiled.

        :param profiles_sampler:

        :param profiler_mode:

        :param profile_lifecycle:

        :param profile_session_sample_rate:

        :param enable_tracing:

        :param propagate_traces:

        :param auto_session_tracking:

        :param spotlight:

        :param instrumenter:

        :param enable_logs: Set `enable_logs` to True to enable the SDK to emit
            Sentry logs. Defaults to False.

        :param before_send_log: An optional function to modify or filter out logs
            before they're sent to Sentry. Any modifications to the log in this
            function will be retained. If the function returns None, the log will
            not be sent to Sentry.

        :param _experiments:
        """
        pass


def _get_default_options():
    # type: () -> dict[str, Any]
    import inspect

    a = inspect.getfullargspec(ClientConstructor.__init__)
    defaults = a.defaults or ()
    kwonlydefaults = a.kwonlydefaults or {}

    return dict(
        itertools.chain(
            zip(a.args[-len(defaults) :], defaults),
            kwonlydefaults.items(),
        )
    )


DEFAULT_OPTIONS = _get_default_options()
del _get_default_options


VERSION = "2.38.0"
