from __future__ import annotations
import contextlib
import functools
import inspect
import os
import re
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, DefaultContext, localcontext
from random import Random
from urllib.parse import quote, unquote

import sentry_sdk
from sentry_sdk.consts import (
    OP,
    SPANDATA,
    SPANSTATUS,
    SPANTEMPLATE,
    BAGGAGE_HEADER_NAME,
    SENTRY_TRACE_HEADER_NAME,
)
from sentry_sdk.utils import (
    capture_internal_exceptions,
    filename_for_module,
    Dsn,
    logger,
    match_regex_list,
    qualname_from_function,
    safe_repr,
    to_string,
    is_sentry_url,
    _is_external_source,
    _is_in_project_root,
    _module_in_list,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType
    from typing import Any, Dict, Generator, Optional, Union


SENTRY_TRACE_REGEX = re.compile(
    "^[ \t]*"  # whitespace
    "([0-9a-f]{32})?"  # trace_id
    "-?([0-9a-f]{16})?"  # span_id
    "-?([01])?"  # sampled
    "[ \t]*$"  # whitespace
)


# This is a normal base64 regex, modified to reflect that fact that we strip the
# trailing = or == off
base64_stripped = (
    # any of the characters in the base64 "alphabet", in multiples of 4
    "([a-zA-Z0-9+/]{4})*"
    # either nothing or 2 or 3 base64-alphabet characters (see
    # https://en.wikipedia.org/wiki/Base64#Decoding_Base64_without_padding for
    # why there's never only 1 extra character)
    "([a-zA-Z0-9+/]{2,3})?"
)


class EnvironHeaders(Mapping):  # type: ignore
    def __init__(
        self,
        environ: Mapping[str, str],
        prefix: str = "HTTP_",
    ) -> None:
        self.environ = environ
        self.prefix = prefix

    def __getitem__(self, key: str) -> Optional[Any]:
        return self.environ[self.prefix + key.replace("-", "_").upper()]

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))

    def __iter__(self) -> Generator[str, None, None]:
        for k in self.environ:
            if not isinstance(k, str):
                continue

            k = k.replace("-", "_").upper()
            if not k.startswith(self.prefix):
                continue

            yield k[len(self.prefix) :]


def has_tracing_enabled(options: dict[str, Any]) -> bool:
    """
    Returns True if either traces_sample_rate or traces_sampler is
    defined.
    """
    if options is None:
        return False

    return bool(
        options.get("traces_sample_rate") is not None
        or options.get("traces_sampler") is not None
    )


@contextlib.contextmanager
def record_sql_queries(
    cursor: Any,
    query: Any,
    params_list: Any,
    paramstyle: Optional[str],
    executemany: bool,
    record_cursor_repr: bool = False,
    span_origin: Optional[str] = None,
) -> Generator[sentry_sdk.tracing.Span, None, None]:
    # TODO: Bring back capturing of params by default
    if sentry_sdk.get_client().options["_experiments"].get("record_sql_params", False):
        if not params_list or params_list == [None]:
            params_list = None

        if paramstyle == "pyformat":
            paramstyle = "format"
    else:
        params_list = None
        paramstyle = None

    query = _format_sql(cursor, query)

    data = {}
    if params_list is not None:
        data["db.params"] = params_list
    if paramstyle is not None:
        data["db.paramstyle"] = paramstyle
    if executemany:
        data["db.executemany"] = True
    if record_cursor_repr and cursor is not None:
        data["db.cursor"] = cursor

    with capture_internal_exceptions():
        sentry_sdk.add_breadcrumb(message=query, category="query", data=data)

    with sentry_sdk.start_span(
        op=OP.DB,
        name=query,
        origin=span_origin,
        only_as_child_span=True,
    ) as span:
        for k, v in data.items():
            span.set_attribute(k, v)
        yield span


def _get_frame_module_abs_path(frame: FrameType) -> Optional[str]:
    try:
        return frame.f_code.co_filename
    except Exception:
        return None


def _should_be_included(
    is_sentry_sdk_frame: bool,
    namespace: Optional[str],
    in_app_include: Optional[list[str]],
    in_app_exclude: Optional[list[str]],
    abs_path: Optional[str],
    project_root: Optional[str],
) -> bool:
    # in_app_include takes precedence over in_app_exclude
    should_be_included = _module_in_list(namespace, in_app_include)
    should_be_excluded = _is_external_source(abs_path) or _module_in_list(
        namespace, in_app_exclude
    )
    return not is_sentry_sdk_frame and (
        should_be_included
        or (_is_in_project_root(abs_path, project_root) and not should_be_excluded)
    )


def add_query_source(span: sentry_sdk.tracing.Span) -> None:
    """
    Adds OTel compatible source code information to the span
    """
    client = sentry_sdk.get_client()
    if not client.is_active():
        return

    if span.start_timestamp is None:
        return

    should_add_query_source = client.options.get("enable_db_query_source", True)
    if not should_add_query_source:
        return

    # We assume here that the query is just ending now. We can't use
    # the actual end timestamp of the span because in OTel the span
    # can't be finished in order to set any attributes on it.
    duration = datetime.now(tz=timezone.utc) - span.start_timestamp
    threshold = client.options.get("db_query_source_threshold_ms", 0)
    slow_query = duration / timedelta(milliseconds=1) > threshold

    if not slow_query:
        return

    project_root = client.options["project_root"]
    in_app_include = client.options.get("in_app_include")
    in_app_exclude = client.options.get("in_app_exclude")

    # Find the correct frame
    frame: Optional[FrameType] = sys._getframe()
    while frame is not None:
        abs_path = _get_frame_module_abs_path(frame)

        try:
            namespace: Optional[str] = frame.f_globals.get("__name__")
        except Exception:
            namespace = None

        is_sentry_sdk_frame = namespace is not None and namespace.startswith(
            "sentry_sdk."
        )

        should_be_included = _should_be_included(
            is_sentry_sdk_frame=is_sentry_sdk_frame,
            namespace=namespace,
            in_app_include=in_app_include,
            in_app_exclude=in_app_exclude,
            abs_path=abs_path,
            project_root=project_root,
        )
        if should_be_included:
            break

        frame = frame.f_back
    else:
        frame = None

    # Set the data
    if frame is not None:
        try:
            lineno = frame.f_lineno
        except Exception:
            lineno = None
        if lineno is not None:
            span.set_attribute(SPANDATA.CODE_LINENO, frame.f_lineno)

        try:
            namespace = frame.f_globals.get("__name__")
        except Exception:
            namespace = None
        if namespace is not None:
            span.set_attribute(SPANDATA.CODE_NAMESPACE, namespace)

        filepath = _get_frame_module_abs_path(frame)
        if filepath is not None:
            if namespace is not None:
                in_app_path = filename_for_module(namespace, filepath)
            elif project_root is not None and filepath.startswith(project_root):
                in_app_path = filepath.replace(project_root, "").lstrip(os.sep)
            else:
                in_app_path = filepath
            span.set_attribute(SPANDATA.CODE_FILEPATH, in_app_path)

        try:
            code_function = frame.f_code.co_name
        except Exception:
            code_function = None

        if code_function is not None:
            span.set_attribute(SPANDATA.CODE_FUNCTION, frame.f_code.co_name)


def extract_sentrytrace_data(
    header: Optional[str],
) -> Optional[Dict[str, Union[str, bool, None]]]:
    """
    Given a `sentry-trace` header string, return a dictionary of data.
    """
    if not header:
        return None

    if header.startswith("00-") and header.endswith("-00"):
        header = header[3:-3]

    match = SENTRY_TRACE_REGEX.match(header)
    if not match:
        return None

    trace_id, parent_span_id, sampled_str = match.groups()
    parent_sampled = None

    if trace_id:
        trace_id = "{:032x}".format(int(trace_id, 16))
    if parent_span_id:
        parent_span_id = "{:016x}".format(int(parent_span_id, 16))
    if sampled_str:
        parent_sampled = sampled_str != "0"

    return {
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "parent_sampled": parent_sampled,
    }


def _format_sql(cursor: Any, sql: str) -> Optional[str]:

    real_sql = None

    # If we're using psycopg2, it could be that we're
    # looking at a query that uses Composed objects. Use psycopg2's mogrify
    # function to format the query. We lose per-parameter trimming but gain
    # accuracy in formatting.
    try:
        if hasattr(cursor, "mogrify"):
            real_sql = cursor.mogrify(sql)
            if isinstance(real_sql, bytes):
                real_sql = real_sql.decode(cursor.connection.encoding)
    except Exception:
        real_sql = None

    return real_sql or to_string(sql)


class PropagationContext:
    """
    The PropagationContext represents the data of a trace in Sentry.
    """

    __slots__ = (
        "_trace_id",
        "_span_id",
        "parent_span_id",
        "parent_sampled",
        "baggage",
    )

    def __init__(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        parent_sampled: Optional[bool] = None,
        baggage: Optional[Baggage] = None,
    ) -> None:
        self._trace_id = trace_id
        """The trace id of the Sentry trace."""

        self._span_id = span_id
        """The span id of the currently executing span."""

        self.parent_span_id = parent_span_id
        """The id of the parent span that started this span.
        The parent span could also be a span in an upstream service."""

        self.parent_sampled = parent_sampled
        """Boolean indicator if the parent span was sampled.
        Important when the parent span originated in an upstream service,
        because we want to sample the whole trace, or nothing from the trace."""

        self.baggage = baggage
        """Baggage object used for dynamic sampling decisions."""

    @property
    def dynamic_sampling_context(self) -> Optional[Dict[str, str]]:
        return self.baggage.dynamic_sampling_context() if self.baggage else None

    @classmethod
    def from_incoming_data(
        cls, incoming_data: Dict[str, Any]
    ) -> Optional[PropagationContext]:
        propagation_context = None

        normalized_data = normalize_incoming_data(incoming_data)
        baggage_header = normalized_data.get(BAGGAGE_HEADER_NAME)
        if baggage_header:
            propagation_context = PropagationContext()
            propagation_context.baggage = Baggage.from_incoming_header(baggage_header)

        sentry_trace_header = normalized_data.get(SENTRY_TRACE_HEADER_NAME)
        if sentry_trace_header:
            sentrytrace_data = extract_sentrytrace_data(sentry_trace_header)
            if sentrytrace_data is not None:
                if propagation_context is None:
                    propagation_context = PropagationContext()
                propagation_context.update(sentrytrace_data)

        if propagation_context is not None:
            propagation_context._fill_sample_rand()

        return propagation_context

    @property
    def trace_id(self) -> str:
        """The trace id of the Sentry trace."""
        if not self._trace_id:
            self._trace_id = uuid.uuid4().hex

        return self._trace_id

    @trace_id.setter
    def trace_id(self, value: str) -> None:
        self._trace_id = value

    @property
    def span_id(self) -> str:
        """The span id of the currently executed span."""
        if not self._span_id:
            self._span_id = uuid.uuid4().hex[16:]

        return self._span_id

    @span_id.setter
    def span_id(self, value: str) -> None:
        self._span_id = value

    def to_traceparent(self) -> str:
        if self.parent_sampled is True:
            sampled = "1"
        elif self.parent_sampled is False:
            sampled = "0"
        else:
            sampled = None

        traceparent = "%s-%s" % (self.trace_id, self.span_id)
        if sampled is not None:
            traceparent += "-%s" % (sampled,)

        return traceparent

    def update(self, other_dict: Dict[str, Any]) -> None:
        """
        Updates the PropagationContext with data from the given dictionary.
        """
        for key, value in other_dict.items():
            try:
                setattr(self, key, value)
            except AttributeError:
                pass

    def _fill_sample_rand(self) -> None:
        """
        Ensure that there is a valid sample_rand value in the baggage.

        If there is a valid sample_rand value in the baggage, we keep it.
        Otherwise, we generate a sample_rand value according to the following:

          - If we have a parent_sampled value and a sample_rate in the DSC, we compute
            a sample_rand value randomly in the range:
                - [0, sample_rate) if parent_sampled is True,
                - or, in the range [sample_rate, 1) if parent_sampled is False.

          - If either parent_sampled or sample_rate is missing, we generate a random
            value in the range [0, 1).

        The sample_rand is deterministically generated from the trace_id, if present.

        This function does nothing if there is no dynamic_sampling_context.
        """
        if self.dynamic_sampling_context is None or self.baggage is None:
            return

        sentry_baggage = self.baggage.sentry_items

        sample_rand = None
        if sentry_baggage.get("sample_rand"):
            try:
                sample_rand = Decimal(sentry_baggage["sample_rand"])
            except Exception:
                logger.debug(
                    f"Failed to convert incoming sample_rand to Decimal: {sample_rand}"
                )

        if sample_rand is not None and 0 <= sample_rand < 1:
            # sample_rand is present and valid, so don't overwrite it
            return

        sample_rate = None
        if sentry_baggage.get("sample_rate"):
            try:
                sample_rate = float(sentry_baggage["sample_rate"])
            except Exception:
                logger.debug(
                    f"Failed to convert incoming sample_rate to float: {sample_rate}"
                )

        lower, upper = _sample_rand_range(self.parent_sampled, sample_rate)

        try:
            sample_rand = _generate_sample_rand(self.trace_id, interval=(lower, upper))
        except ValueError:
            # ValueError is raised if the interval is invalid, i.e. lower >= upper.
            # lower >= upper might happen if the incoming trace's sampled flag
            # and sample_rate are inconsistent, e.g. sample_rate=0.0 but sampled=True.
            # We cannot generate a sensible sample_rand value in this case.
            logger.debug(
                f"Could not backfill sample_rand, since parent_sampled={self.parent_sampled} "
                f"and sample_rate={sample_rate}."
            )
            return

        self.baggage.sentry_items["sample_rand"] = f"{sample_rand:.6f}"  # noqa: E231

    def _sample_rand(self) -> Optional[str]:
        """Convenience method to get the sample_rand value from the baggage."""
        if self.baggage is None:
            return None

        return self.baggage.sentry_items.get("sample_rand")

    def __repr__(self) -> str:
        return "<PropagationContext _trace_id={} _span_id={} parent_span_id={} parent_sampled={} baggage={} dynamic_sampling_context={}>".format(
            self._trace_id,
            self._span_id,
            self.parent_span_id,
            self.parent_sampled,
            self.baggage,
            self.dynamic_sampling_context,
        )


class Baggage:
    """
    The W3C Baggage header information (see https://www.w3.org/TR/baggage/).

    Before mutating a `Baggage` object, calling code must check that `mutable` is `True`.
    Mutating a `Baggage` object that has `mutable` set to `False` is not allowed, but
    it is the caller's responsibility to enforce this restriction.
    """

    __slots__ = ("sentry_items", "third_party_items", "mutable")

    SENTRY_PREFIX = "sentry-"
    SENTRY_PREFIX_REGEX = re.compile("^sentry-")

    def __init__(
        self,
        sentry_items: Dict[str, str],
        third_party_items: str = "",
        mutable: bool = True,
    ) -> None:
        self.sentry_items = sentry_items
        self.third_party_items = third_party_items
        self.mutable = mutable

    @classmethod
    def from_incoming_header(
        cls,
        header: Optional[str],
    ) -> Baggage:
        """
        freeze if incoming header already has sentry baggage
        """
        sentry_items = {}
        third_party_items = ""
        mutable = True

        if header:
            for item in header.split(","):
                if "=" not in item:
                    continue

                with capture_internal_exceptions():
                    item = item.strip()
                    key, val = item.split("=")
                    if Baggage.SENTRY_PREFIX_REGEX.match(key):
                        baggage_key = unquote(key.split("-")[1])
                        sentry_items[baggage_key] = unquote(val)
                        mutable = False
                    else:
                        third_party_items += ("," if third_party_items else "") + item

        return Baggage(sentry_items, third_party_items, mutable)

    @classmethod
    def from_options(cls, scope: sentry_sdk.scope.Scope) -> Optional[Baggage]:
        sentry_items: Dict[str, str] = {}
        third_party_items = ""
        mutable = False

        client = sentry_sdk.get_client()

        if not client.is_active() or scope._propagation_context is None:
            return Baggage(sentry_items)

        options = client.options
        propagation_context = scope._propagation_context

        if propagation_context is not None:
            sentry_items["trace_id"] = propagation_context.trace_id

        if options.get("environment"):
            sentry_items["environment"] = options["environment"]

        if options.get("release"):
            sentry_items["release"] = options["release"]

        if options.get("dsn"):
            sentry_items["public_key"] = Dsn(options["dsn"]).public_key

        if options.get("traces_sample_rate"):
            sentry_items["sample_rate"] = str(options["traces_sample_rate"])

        return Baggage(sentry_items, third_party_items, mutable)

    def freeze(self) -> None:
        self.mutable = False

    def dynamic_sampling_context(self) -> Dict[str, str]:
        header = {}

        for key, item in self.sentry_items.items():
            header[key] = item

        return header

    def serialize(self, include_third_party: bool = False) -> str:
        items = []

        for key, val in self.sentry_items.items():
            with capture_internal_exceptions():
                item = Baggage.SENTRY_PREFIX + quote(key) + "=" + quote(str(val))
                items.append(item)

        if include_third_party:
            items.append(self.third_party_items)

        return ",".join(items)

    @staticmethod
    def strip_sentry_baggage(header: str) -> str:
        """Remove Sentry baggage from the given header.

        Given a Baggage header, return a new Baggage header with all Sentry baggage items removed.
        """
        return ",".join(
            (
                item
                for item in header.split(",")
                if not Baggage.SENTRY_PREFIX_REGEX.match(item.strip())
            )
        )

    def __repr__(self) -> str:
        return f'<Baggage "{self.serialize(include_third_party=True)}", mutable={self.mutable}>'


def should_propagate_trace(client: sentry_sdk.client.BaseClient, url: str) -> bool:
    """
    Returns True if url matches trace_propagation_targets configured in the given client. Otherwise, returns False.
    """
    trace_propagation_targets = client.options["trace_propagation_targets"]

    if is_sentry_url(client, url):
        return False

    return match_regex_list(url, trace_propagation_targets, substring_matching=True)


def _is_span_origin_excluded(origin: Optional[str]) -> bool:
    """
    Check if spans with this origin should be ignored based on the `exclude_span_origins` option.
    """
    if origin is None:
        return False

    client = sentry_sdk.get_client()
    exclude_span_origins = client.options.get("exclude_span_origins")
    if not exclude_span_origins:
        return False

    return match_regex_list(origin, exclude_span_origins, substring_matching=True)


def normalize_incoming_data(incoming_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalizes incoming data so the keys are all lowercase with dashes instead of underscores and stripped from known prefixes.
    """
    data = {}
    for key, value in incoming_data.items():
        if key.startswith("HTTP_"):
            key = key[5:]

        key = key.replace("_", "-").lower()
        data[key] = value

    return data


def create_span_decorator(
    op: Optional[Union[str, OP]] = None,
    name: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
    template: SPANTEMPLATE = SPANTEMPLATE.DEFAULT,
) -> Any:
    """
    Create a span decorator that can wrap both sync and async functions.

    :param op: The operation type for the span.
    :type op: str or :py:class:`sentry_sdk.consts.OP` or None
    :param name: The name of the span.
    :type name: str or None
    :param attributes: Additional attributes to set on the span.
    :type attributes: dict or None
    :param template: The type of span to create. This determines what kind of
        span instrumentation and data collection will be applied. Use predefined
        constants from :py:class:`sentry_sdk.consts.SPANTEMPLATE`.
        The default is `SPANTEMPLATE.DEFAULT` which is the right choice for most
        use cases.
    :type template: :py:class:`sentry_sdk.consts.SPANTEMPLATE`
    """
    from sentry_sdk.scope import should_send_default_pii

    def span_decorator(f: Any) -> Any:
        """
        Decorator to create a span for the given function.
        """

        @functools.wraps(f)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            span = get_current_span()

            if span is None:
                logger.debug(
                    "Cannot create a child span for %s. "
                    "Please start a Sentry transaction before calling this function.",
                    qualname_from_function(f),
                )
                return await f(*args, **kwargs)

            span_op = op or _get_span_op(template)
            function_name = name or qualname_from_function(f) or ""
            span_name = _get_span_name(template, function_name, kwargs)
            send_pii = should_send_default_pii()

            with sentry_sdk.start_span(
                op=span_op,
                name=span_name,
                only_as_child_span=True,
            ) as span:
                span.set_attributes(attributes or {})
                _set_input_attributes(
                    span, template, send_pii, function_name, f, args, kwargs
                )

                result = await f(*args, **kwargs)

                _set_output_attributes(span, template, send_pii, result)

                return result

        try:
            async_wrapper.__signature__ = inspect.signature(f)  # type: ignore[attr-defined]
        except Exception:
            pass

        @functools.wraps(f)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            span = get_current_span()

            if span is None:
                logger.debug(
                    "Cannot create a child span for %s. "
                    "Please start a Sentry transaction before calling this function.",
                    qualname_from_function(f),
                )
                return f(*args, **kwargs)

            span_op = op or _get_span_op(template)
            function_name = name or qualname_from_function(f) or ""
            span_name = _get_span_name(template, function_name, kwargs)
            send_pii = should_send_default_pii()

            with sentry_sdk.start_span(
                op=span_op,
                name=span_name,
                only_as_child_span=True,
            ) as span:
                span.set_attributes(attributes or {})
                _set_input_attributes(
                    span, template, send_pii, function_name, f, args, kwargs
                )

                result = f(*args, **kwargs)

                _set_output_attributes(span, template, send_pii, result)

                return result

        try:
            sync_wrapper.__signature__ = inspect.signature(f)  # type: ignore[attr-defined]
        except Exception:
            pass

        if inspect.iscoroutinefunction(f):
            return async_wrapper
        else:
            return sync_wrapper

    return span_decorator


def get_current_span(
    scope: Optional[sentry_sdk.scope.Scope] = None,
) -> Optional[sentry_sdk.tracing.Span]:
    """
    Returns the currently active span if there is one running, otherwise `None`
    """
    scope = scope or sentry_sdk.get_current_scope()
    current_span = scope.span
    return current_span


def _generate_sample_rand(
    trace_id: Optional[str],
    interval: tuple[float, float] = (0.0, 1.0),
) -> Decimal:
    """Generate a sample_rand value from a trace ID.

    The generated value will be pseudorandomly chosen from the provided
    interval. Specifically, given (lower, upper) = interval, the generated
    value will be in the range [lower, upper). The value has 6-digit precision,
    so when printing with .6f, the value will never be rounded up.

    The pseudorandom number generator is seeded with the trace ID.
    """
    lower, upper = interval
    if not lower < upper:  # using `if lower >= upper` would handle NaNs incorrectly
        raise ValueError("Invalid interval: lower must be less than upper")

    rng = Random(trace_id)
    sample_rand = upper
    while sample_rand >= upper:
        sample_rand = rng.uniform(lower, upper)

    # Round down to exactly six decimal-digit precision.
    # Setting the context is needed to avoid an InvalidOperation exception
    # in case the user has changed the default precision or set traps.
    with localcontext(DefaultContext) as ctx:
        ctx.prec = 6
        return Decimal(sample_rand).quantize(
            Decimal("0.000001"),
            rounding=ROUND_DOWN,
        )


def _sample_rand_range(
    parent_sampled: Optional[bool], sample_rate: Optional[float]
) -> tuple[float, float]:
    """
    Compute the lower (inclusive) and upper (exclusive) bounds of the range of values
    that a generated sample_rand value must fall into, given the parent_sampled and
    sample_rate values.
    """
    if parent_sampled is None or sample_rate is None:
        return 0.0, 1.0
    elif parent_sampled is True:
        return 0.0, sample_rate
    else:  # parent_sampled is False
        return sample_rate, 1.0


def _get_value(source: Any, key: str) -> Optional[Any]:
    """
    Gets a value from a source object. The source can be a dict or an object.
    It is checked for dictionary keys and object attributes.
    """
    value = None
    if isinstance(source, dict):
        value = source.get(key)
    else:
        if hasattr(source, key):
            try:
                value = getattr(source, key)
            except Exception:
                value = None
    return value


def _get_span_name(
    template: Union[str, SPANTEMPLATE],
    name: str,
    kwargs: Optional[dict[str, Any]] = None,
) -> str:
    """
    Get the name of the span based on the template and the name.
    """
    span_name = name

    if template == SPANTEMPLATE.AI_CHAT:
        model = None
        if kwargs:
            for key in ("model", "model_name"):
                if kwargs.get(key) and isinstance(kwargs[key], str):
                    model = kwargs[key]
                    break

        span_name = f"chat {model}" if model else "chat"

    elif template == SPANTEMPLATE.AI_AGENT:
        span_name = f"invoke_agent {name}"

    elif template == SPANTEMPLATE.AI_TOOL:
        span_name = f"execute_tool {name}"

    return span_name


def _get_span_op(template: Union[str, SPANTEMPLATE]) -> str:
    """
    Get the operation of the span based on the template.
    """
    mapping = {
        SPANTEMPLATE.AI_CHAT: OP.GEN_AI_CHAT,
        SPANTEMPLATE.AI_AGENT: OP.GEN_AI_INVOKE_AGENT,
        SPANTEMPLATE.AI_TOOL: OP.GEN_AI_EXECUTE_TOOL,
    }  # type: dict[Union[str, SPANTEMPLATE], Union[str, OP]]
    op = mapping.get(template, OP.FUNCTION)

    return str(op)


def _get_input_attributes(
    template: Union[str, SPANTEMPLATE],
    send_pii: bool,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    Get input attributes for the given span template.
    """
    attributes: dict[str, Any] = {}

    if template in [SPANTEMPLATE.AI_AGENT, SPANTEMPLATE.AI_TOOL, SPANTEMPLATE.AI_CHAT]:
        mapping: dict[str, tuple[str, type]] = {
            "model": (SPANDATA.GEN_AI_REQUEST_MODEL, str),
            "model_name": (SPANDATA.GEN_AI_REQUEST_MODEL, str),
            "agent": (SPANDATA.GEN_AI_AGENT_NAME, str),
            "agent_name": (SPANDATA.GEN_AI_AGENT_NAME, str),
            "max_tokens": (SPANDATA.GEN_AI_REQUEST_MAX_TOKENS, int),
            "frequency_penalty": (SPANDATA.GEN_AI_REQUEST_FREQUENCY_PENALTY, float),
            "presence_penalty": (SPANDATA.GEN_AI_REQUEST_PRESENCE_PENALTY, float),
            "temperature": (SPANDATA.GEN_AI_REQUEST_TEMPERATURE, float),
            "top_p": (SPANDATA.GEN_AI_REQUEST_TOP_P, float),
            "top_k": (SPANDATA.GEN_AI_REQUEST_TOP_K, int),
        }

        def _set_from_key(key: str, value: Any) -> None:
            if key in mapping:
                (attribute, data_type) = mapping[key]
                if value is not None and isinstance(value, data_type):
                    attributes[attribute] = value

        for key, value in list(kwargs.items()):
            if key == "prompt" and isinstance(value, str):
                attributes.setdefault(SPANDATA.GEN_AI_REQUEST_MESSAGES, []).append(
                    {"role": "user", "content": value}
                )
                continue

            if key == "system_prompt" and isinstance(value, str):
                attributes.setdefault(SPANDATA.GEN_AI_REQUEST_MESSAGES, []).append(
                    {"role": "system", "content": value}
                )
                continue

            _set_from_key(key, value)

    if template == SPANTEMPLATE.AI_TOOL and send_pii:
        attributes[SPANDATA.GEN_AI_TOOL_INPUT] = safe_repr(
            {"args": args, "kwargs": kwargs}
        )

    # Coerce to string
    if SPANDATA.GEN_AI_REQUEST_MESSAGES in attributes:
        attributes[SPANDATA.GEN_AI_REQUEST_MESSAGES] = safe_repr(
            attributes[SPANDATA.GEN_AI_REQUEST_MESSAGES]
        )

    return attributes


def _get_usage_attributes(usage: Any) -> dict[str, Any]:
    """
    Get usage attributes.
    """
    attributes = {}

    def _set_from_keys(attribute: str, keys: tuple[str, ...]) -> None:
        for key in keys:
            value = _get_value(usage, key)
            if value is not None and isinstance(value, int):
                attributes[attribute] = value

    _set_from_keys(
        SPANDATA.GEN_AI_USAGE_INPUT_TOKENS,
        ("prompt_tokens", "input_tokens"),
    )
    _set_from_keys(
        SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS,
        ("completion_tokens", "output_tokens"),
    )
    _set_from_keys(
        SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS,
        ("total_tokens",),
    )

    return attributes


def _get_output_attributes(
    template: Union[str, SPANTEMPLATE], send_pii: bool, result: Any
) -> dict[str, Any]:
    """
    Get output attributes for the given span template.
    """
    attributes = {}  # type: dict[str, Any]

    if template in [SPANTEMPLATE.AI_AGENT, SPANTEMPLATE.AI_TOOL, SPANTEMPLATE.AI_CHAT]:
        with capture_internal_exceptions():
            # Usage from result, result.usage, and result.metadata.usage
            usage_candidates = [result]

            usage = _get_value(result, "usage")
            usage_candidates.append(usage)

            meta = _get_value(result, "metadata")
            usage = _get_value(meta, "usage")
            usage_candidates.append(usage)

            for usage_candidate in usage_candidates:
                if usage_candidate is not None:
                    attributes.update(_get_usage_attributes(usage_candidate))

            # Response model
            model_name = _get_value(result, "model")
            if model_name is not None and isinstance(model_name, str):
                attributes[SPANDATA.GEN_AI_RESPONSE_MODEL] = model_name

            model_name = _get_value(result, "model_name")
            if model_name is not None and isinstance(model_name, str):
                attributes[SPANDATA.GEN_AI_RESPONSE_MODEL] = model_name

    # Tool output
    if template == SPANTEMPLATE.AI_TOOL and send_pii:
        attributes[SPANDATA.GEN_AI_TOOL_OUTPUT] = safe_repr(result)

    return attributes


def _set_input_attributes(
    span: sentry_sdk.tracing.Span,
    template: Union[str, SPANTEMPLATE],
    send_pii: bool,
    name: str,
    f: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """
    Set span input attributes based on the given span template.

    :param span: The span to set attributes on.
    :param template: The template to use to set attributes on the span.
    :param send_pii: Whether to send PII data.
    :param f: The wrapped function.
    :param args: The arguments to the wrapped function.
    :param kwargs: The keyword arguments to the wrapped function.
    """
    attributes = {}  # type: dict[str, Any]

    if template == SPANTEMPLATE.AI_AGENT:
        attributes = {
            SPANDATA.GEN_AI_OPERATION_NAME: "invoke_agent",
            SPANDATA.GEN_AI_AGENT_NAME: name,
        }
    elif template == SPANTEMPLATE.AI_CHAT:
        attributes = {
            SPANDATA.GEN_AI_OPERATION_NAME: "chat",
        }
    elif template == SPANTEMPLATE.AI_TOOL:
        attributes = {
            SPANDATA.GEN_AI_OPERATION_NAME: "execute_tool",
            SPANDATA.GEN_AI_TOOL_NAME: name,
        }

        docstring = f.__doc__
        if docstring is not None:
            attributes[SPANDATA.GEN_AI_TOOL_DESCRIPTION] = docstring

    attributes.update(_get_input_attributes(template, send_pii, args, kwargs))
    span.set_attributes(attributes or {})


def _set_output_attributes(
    span: sentry_sdk.tracing.Span,
    template: Union[str, SPANTEMPLATE],
    send_pii: bool,
    result: Any,
) -> None:
    """
    Set span output attributes based on the given span template.

    :param span: The span to set attributes on.
    :param template: The template to use to set attributes on the span.
    :param send_pii: Whether to send PII data.
    :param result: The result of the wrapped function.
    """
    span.set_attributes(_get_output_attributes(template, send_pii, result) or {})


def get_span_status_from_http_code(http_status_code: int) -> str:
    """
    Returns the Sentry status corresponding to the given HTTP status code.

    See: https://develop.sentry.dev/sdk/event-payloads/contexts/#trace-context
    """
    if http_status_code < 400:
        return SPANSTATUS.OK

    elif 400 <= http_status_code < 500:
        if http_status_code == 403:
            return SPANSTATUS.PERMISSION_DENIED
        elif http_status_code == 404:
            return SPANSTATUS.NOT_FOUND
        elif http_status_code == 429:
            return SPANSTATUS.RESOURCE_EXHAUSTED
        elif http_status_code == 413:
            return SPANSTATUS.FAILED_PRECONDITION
        elif http_status_code == 401:
            return SPANSTATUS.UNAUTHENTICATED
        elif http_status_code == 409:
            return SPANSTATUS.ALREADY_EXISTS
        else:
            return SPANSTATUS.INVALID_ARGUMENT

    elif 500 <= http_status_code < 600:
        if http_status_code == 504:
            return SPANSTATUS.DEADLINE_EXCEEDED
        elif http_status_code == 501:
            return SPANSTATUS.UNIMPLEMENTED
        elif http_status_code == 503:
            return SPANSTATUS.UNAVAILABLE
        else:
            return SPANSTATUS.INTERNAL_ERROR

    return SPANSTATUS.UNKNOWN_ERROR
