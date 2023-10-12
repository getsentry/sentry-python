import os
import io
import re
import threading
import random
import time
import zlib
from functools import wraps, partial
from threading import Event, Lock, Thread
from contextlib import contextmanager

from sentry_sdk._compat import text_type
from sentry_sdk.hub import Hub
from sentry_sdk.utils import now, nanosecond_time
from sentry_sdk.envelope import Envelope, Item
from sentry_sdk.tracing import (
    TRANSACTION_SOURCE_ROUTE,
    TRANSACTION_SOURCE_VIEW,
    TRANSACTION_SOURCE_COMPONENT,
    TRANSACTION_SOURCE_TASK,
)
from sentry_sdk._types import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import Iterable
    from typing import Callable
    from typing import Optional
    from typing import Generator
    from typing import Tuple

    from sentry_sdk._types import BucketKey
    from sentry_sdk._types import DurationUnit
    from sentry_sdk._types import FlushedMetricValue
    from sentry_sdk._types import MeasurementUnit
    from sentry_sdk._types import MetricTagValue
    from sentry_sdk._types import MetricTags
    from sentry_sdk._types import MetricTagsInternal
    from sentry_sdk._types import MetricType
    from sentry_sdk._types import MetricValue


_thread_local = threading.local()
_sanitize_key = partial(re.compile(r"[^a-zA-Z0-9_/.-]+").sub, "_")
_sanitize_value = partial(re.compile(r"[^\w\d_:/@\.{}\[\]$-]+", re.UNICODE).sub, "_")

GOOD_TRANSACTION_SOURCES = frozenset(
    [
        TRANSACTION_SOURCE_ROUTE,
        TRANSACTION_SOURCE_VIEW,
        TRANSACTION_SOURCE_COMPONENT,
        TRANSACTION_SOURCE_TASK,
    ]
)


@contextmanager
def recursion_protection():
    # type: () -> Generator[bool, None, None]
    """Enters recursion protection and returns the old flag."""
    try:
        in_metrics = _thread_local.in_metrics
    except AttributeError:
        in_metrics = False
    _thread_local.in_metrics = True
    try:
        yield in_metrics
    finally:
        _thread_local.in_metrics = in_metrics


def metrics_noop(func):
    # type: (Any) -> Any
    """Convenient decorator that uses `recursion_protection` to
    make a function a noop.
    """

    @wraps(func)
    def new_func(*args, **kwargs):
        # type: (*Any, **Any) -> Any
        with recursion_protection() as in_metrics:
            if not in_metrics:
                return func(*args, **kwargs)

    return new_func


class Metric(object):
    __slots__ = ()

    @property
    def weight(self):
        # type: (...) -> int
        raise NotImplementedError()

    def add(
        self, value  # type: MetricValue
    ):
        # type: (...) -> None
        raise NotImplementedError()

    def serialize_value(self):
        # type: (...) -> Iterable[FlushedMetricValue]
        raise NotImplementedError()


class CounterMetric(Metric):
    __slots__ = ("value",)

    def __init__(
        self, first  # type: MetricValue
    ):
        # type: (...) -> None
        self.value = float(first)

    @property
    def weight(self):
        # type: (...) -> int
        return 1

    def add(
        self, value  # type: MetricValue
    ):
        # type: (...) -> None
        self.value += float(value)

    def serialize_value(self):
        # type: (...) -> Iterable[FlushedMetricValue]
        return (self.value,)


class GaugeMetric(Metric):
    __slots__ = (
        "last",
        "min",
        "max",
        "sum",
        "count",
    )

    def __init__(
        self, first  # type: MetricValue
    ):
        # type: (...) -> None
        first = float(first)
        self.last = first
        self.min = first
        self.max = first
        self.sum = first
        self.count = 1

    @property
    def weight(self):
        # type: (...) -> int
        # Number of elements.
        return 5

    def add(
        self, value  # type: MetricValue
    ):
        # type: (...) -> None
        value = float(value)
        self.last = value
        self.min = min(self.min, value)
        self.max = max(self.max, value)
        self.sum += value
        self.count += 1

    def serialize_value(self):
        # type: (...) -> Iterable[FlushedMetricValue]
        return (
            self.last,
            self.min,
            self.max,
            self.sum,
            self.count,
        )


class DistributionMetric(Metric):
    __slots__ = ("value",)

    def __init__(
        self, first  # type: MetricValue
    ):
        # type(...) -> None
        self.value = [float(first)]

    @property
    def weight(self):
        # type: (...) -> int
        return len(self.value)

    def add(
        self, value  # type: MetricValue
    ):
        # type: (...) -> None
        self.value.append(float(value))

    def serialize_value(self):
        # type: (...) -> Iterable[FlushedMetricValue]
        return self.value


class SetMetric(Metric):
    __slots__ = ("value",)

    def __init__(
        self, first  # type: MetricValue
    ):
        # type: (...) -> None
        self.value = {first}

    @property
    def weight(self):
        # type: (...) -> int
        return len(self.value)

    def add(
        self, value  # type: MetricValue
    ):
        # type: (...) -> None
        self.value.add(value)

    def serialize_value(self):
        # type: (...) -> Iterable[FlushedMetricValue]
        def _hash(x):
            # type: (MetricValue) -> int
            if isinstance(x, str):
                return zlib.crc32(x.encode("utf-8")) & 0xFFFFFFFF
            return int(x)

        return (_hash(value) for value in self.value)


def _encode_metrics(flushable_buckets):
    # type: (Iterable[Tuple[int, Dict[BucketKey, Metric]]]) -> bytes
    out = io.BytesIO()
    _write = out.write

    # Note on sanetization: we intentionally sanetize in emission (serialization)
    # and not during aggregation for performance reasons.  This means that the
    # envelope can in fact have duplicate buckets stored.  This is acceptable for
    # relay side emission and should not happen commonly.

    for timestamp, buckets in flushable_buckets:
        for bucket_key, metric in buckets.items():
            metric_type, metric_name, metric_unit, metric_tags = bucket_key
            metric_name = _sanitize_key(metric_name)
            _write(metric_name.encode("utf-8"))
            _write(b"@")
            _write(metric_unit.encode("utf-8"))

            for serialized_value in metric.serialize_value():
                _write(b":")
                _write(str(serialized_value).encode("utf-8"))

            _write(b"|")
            _write(metric_type.encode("ascii"))

            if metric_tags:
                _write(b"|#")
                first = True
                for tag_key, tag_value in metric_tags:
                    tag_key = _sanitize_key(tag_key)
                    if not tag_key:
                        continue
                    if first:
                        first = False
                    else:
                        _write(b",")
                    _write(tag_key.encode("utf-8"))
                    _write(b":")
                    _write(_sanitize_value(tag_value).encode("utf-8"))

            _write(b"|T")
            _write(str(timestamp).encode("ascii"))
            _write(b"\n")

    return out.getvalue()


METRIC_TYPES = {
    "c": CounterMetric,
    "g": GaugeMetric,
    "d": DistributionMetric,
    "s": SetMetric,
}

# some of these are dumb
TIMING_FUNCTIONS = {
    "nanosecond": nanosecond_time,
    "microsecond": lambda: nanosecond_time() / 1000.0,
    "millisecond": lambda: nanosecond_time() / 1000000.0,
    "second": now,
    "minute": lambda: now() / 60.0,
    "hour": lambda: now() / 3600.0,
    "day": lambda: now() / 3600.0 / 24.0,
    "week": lambda: now() / 3600.0 / 24.0 / 7.0,
}


class MetricsAggregator(object):
    ROLLUP_IN_SECONDS = 10.0
    MAX_WEIGHT = 100000
    FLUSHER_SLEEP_TIME = 5.0

    def __init__(
        self,
        capture_func,  # type: Callable[[Envelope], None]
    ):
        # type: (...) -> None
        self.buckets = {}  # type: Dict[int, Any]
        self._buckets_total_weight = 0
        self._capture_func = capture_func
        self._lock = Lock()
        self._running = True
        self._flush_event = Event()
        self._force_flush = False

        # The aggregator shifts it's flushing by up to an entire rollup window to
        # avoid multiple clients trampling on end of a 10 second window as all the
        # buckets are anchored to multiples of ROLLUP seconds.  We randomize this
        # number once per aggregator boot to achieve some level of offsetting
        # across a fleet of deployed SDKs.  Relay itself will also apply independent
        # jittering.
        self._flush_shift = random.random() * self.ROLLUP_IN_SECONDS

        self._flusher = None  # type: Optional[Thread]
        self._flusher_pid = None  # type: Optional[int]
        self._ensure_thread()

    def _ensure_thread(self):
        # type: (...) -> None
        """For forking processes we might need to restart this thread.
        This ensures that our process actually has that thread running.
        """
        pid = os.getpid()
        if self._flusher_pid == pid:
            return
        with self._lock:
            self._flusher_pid = pid
            self._flusher = Thread(target=self._flush_loop)
            self._flusher.daemon = True
            self._flusher.start()

    def _flush_loop(self):
        # type: (...) -> None
        _thread_local.in_metrics = True
        while self._running or self._force_flush:
            self._flush()
            if self._running:
                self._flush_event.wait(self.FLUSHER_SLEEP_TIME)

    def _flush(self):
        # type: (...) -> None
        flushable_buckets = self._flushable_buckets()
        if flushable_buckets:
            self._emit(flushable_buckets)

    def _flushable_buckets(self):
        # type: (...) -> (Iterable[Tuple[int, Dict[BucketKey, Metric]]])
        with self._lock:
            force_flush = self._force_flush
            cutoff = time.time() - self.ROLLUP_IN_SECONDS - self._flush_shift
            flushable_buckets = ()  # type: Iterable[Tuple[int, Dict[BucketKey, Metric]]]
            weight_to_remove = 0

            if force_flush:
                flushable_buckets = self.buckets.items()
                self.buckets = {}
                self._buckets_total_weight = 0
                self._force_flush = False
            else:
                flushable_buckets = []
                for buckets_timestamp, buckets in self.buckets.items():
                    # If the timestamp of the bucket is newer that the rollup we want to skip it.
                    if buckets_timestamp <= cutoff:
                        flushable_buckets.append((buckets_timestamp, buckets))

                # We will clear the elements while holding the lock, in order to avoid requesting it downstream again.
                for buckets_timestamp, buckets in flushable_buckets:
                    for _, metric in buckets.items():
                        weight_to_remove += metric.weight
                    del self.buckets[buckets_timestamp]

                self._buckets_total_weight -= weight_to_remove

        return flushable_buckets

    @metrics_noop
    def add(
        self,
        ty,  # type: MetricType
        key,  # type: str
        value,  # type: MetricValue
        unit,  # type: MeasurementUnit
        tags,  # type: Optional[MetricTags]
        timestamp=None,  # type: Optional[float]
    ):
        # type: (...) -> None
        self._ensure_thread()

        if self._flusher is None:
            return

        if timestamp is None:
            timestamp = time.time()

        bucket_timestamp = int(
            (timestamp // self.ROLLUP_IN_SECONDS) * self.ROLLUP_IN_SECONDS
        )
        bucket_key = (
            ty,
            key,
            unit,
            self._serialize_tags(tags),
        )

        with self._lock:
            local_buckets = self.buckets.setdefault(bucket_timestamp, {})
            metric = local_buckets.get(bucket_key)
            if metric is not None:
                previous_weight = metric.weight
                metric.add(value)
            else:
                metric = local_buckets[bucket_key] = METRIC_TYPES[ty](value)
                previous_weight = 0

            self._buckets_total_weight += metric.weight - previous_weight

        # Given the new weight we consider whether we want to force flush.
        self._consider_force_flush()

    def kill(self):
        # type: (...) -> None
        if self._flusher is None:
            return

        self._running = False
        self._flush_event.set()
        self._flusher.join()
        self._flusher = None

    @metrics_noop
    def flush(self):
        # type: (...) -> None
        self._force_flush = True
        self._flush()

    def _consider_force_flush(self):
        # type: (...) -> None
        # It's important to acquire a lock around this method, since it will touch shared data structures.
        total_weight = len(self.buckets) + self._buckets_total_weight
        if total_weight >= self.MAX_WEIGHT:
            self._force_flush = True
            self._flush_event.set()

    def _emit(
        self,
        flushable_buckets,  # type: (Iterable[Tuple[int, Dict[BucketKey, Metric]]])
    ):
        # type: (...) -> Envelope
        encoded_metrics = _encode_metrics(flushable_buckets)
        metric_item = Item(payload=encoded_metrics, type="statsd")
        envelope = Envelope(items=[metric_item])
        self._capture_func(envelope)
        return envelope

    def _serialize_tags(
        self, tags  # type: Optional[MetricTags]
    ):
        # type: (...) -> MetricTagsInternal
        if not tags:
            return ()

        rv = []
        for key, value in tags.items():
            # If the value is a collection, we want to flatten it.
            if isinstance(value, (list, tuple)):
                for inner_value in value:
                    if inner_value is not None:
                        rv.append((key, text_type(inner_value)))
            elif value is not None:
                rv.append((key, text_type(value)))

        # It's very important to sort the tags in order to obtain the
        # same bucket key.
        return tuple(sorted(rv))


def _get_aggregator_and_update_tags(key, tags):
    # type: (str, Optional[MetricTags]) -> Tuple[Optional[MetricsAggregator], Optional[MetricTags]]
    """Returns the current metrics aggregator if there is one."""
    hub = Hub.current
    client = hub.client
    if client is None or client.metrics_aggregator is None:
        return None, tags

    updated_tags = dict(tags or ())  # type: Dict[str, MetricTagValue]
    updated_tags.setdefault("release", client.options["release"])
    updated_tags.setdefault("environment", client.options["environment"])

    scope = hub.scope
    transaction_source = scope._transaction_info.get("source")
    if transaction_source in GOOD_TRANSACTION_SOURCES:
        transaction = scope._transaction
        if transaction:
            updated_tags.setdefault("transaction", transaction)

    callback = client.options.get("_experiments", {}).get("before_emit_metric")
    if callback is not None:
        with recursion_protection() as in_metrics:
            if not in_metrics:
                if not callback(key, updated_tags):
                    return None, updated_tags

    return client.metrics_aggregator, updated_tags


def incr(
    key,  # type: str
    value=1.0,  # type: float
    unit="none",  # type: MeasurementUnit
    tags=None,  # type: Optional[MetricTags]
    timestamp=None,  # type: Optional[float]
):
    # type: (...) -> None
    """Increments a counter."""
    aggregator, tags = _get_aggregator_and_update_tags(key, tags)
    if aggregator is not None:
        aggregator.add("c", key, value, unit, tags, timestamp)


class _Timing(object):
    def __init__(
        self,
        key,  # type: str
        tags,  # type: Optional[MetricTags]
        timestamp,  # type: Optional[float]
        value,  # type: Optional[float]
        unit,  # type: DurationUnit
    ):
        # type: (...) -> None
        self.key = key
        self.tags = tags
        self.timestamp = timestamp
        self.value = value
        self.unit = unit
        self.entered = None  # type: Optional[float]

    def _validate_invocation(self, context):
        # type: (str) -> None
        if self.value is not None:
            raise TypeError(
                "cannot use timing as %s when a value is provided" % context
            )

    def __enter__(self):
        # type: (...) -> _Timing
        self.entered = TIMING_FUNCTIONS[self.unit]()
        self._validate_invocation("context-manager")
        return self

    def __exit__(self, exc_type, exc_value, tb):
        # type: (Any, Any, Any) -> None
        aggregator, tags = _get_aggregator_and_update_tags(self.key, self.tags)
        if aggregator is not None:
            elapsed = TIMING_FUNCTIONS[self.unit]() - self.entered  # type: ignore
            aggregator.add("d", self.key, elapsed, self.unit, tags, self.timestamp)

    def __call__(self, f):
        # type: (Any) -> Any
        self._validate_invocation("decorator")

        @wraps(f)
        def timed_func(*args, **kwargs):
            # type: (*Any, **Any) -> Any
            with timing(
                key=self.key, tags=self.tags, timestamp=self.timestamp, unit=self.unit
            ):
                return f(*args, **kwargs)

        return timed_func


def timing(
    key,  # type: str
    value=None,  # type: Optional[float]
    unit="second",  # type: DurationUnit
    tags=None,  # type: Optional[MetricTags]
    timestamp=None,  # type: Optional[float]
):
    # type: (...) -> _Timing
    """Emits a distribution with the time it takes to run the given code block.

    This method supports three forms of invocation:

    - when a `value` is provided, it functions similar to `distribution` but with
    - it can be used as a context manager
    - it can be used as a decorator
    """
    if value is not None:
        aggregator, tags = _get_aggregator_and_update_tags(key, tags)
        if aggregator is not None:
            aggregator.add("d", key, value, unit, tags, timestamp)
    return _Timing(key, tags, timestamp, value, unit)


def distribution(
    key,  # type: str
    value,  # type: float
    unit="none",  # type: MeasurementUnit
    tags=None,  # type: Optional[MetricTags]
    timestamp=None,  # type: Optional[float]
):
    # type: (...) -> None
    """Emits a distribution."""
    aggregator, tags = _get_aggregator_and_update_tags(key, tags)
    if aggregator is not None:
        aggregator.add("d", key, value, unit, tags, timestamp)


def set(
    key,  # type: str
    value,  # type: MetricValue
    unit="none",  # type: MeasurementUnit
    tags=None,  # type: Optional[MetricTags]
    timestamp=None,  # type: Optional[float]
):
    # type: (...) -> None
    """Emits a set."""
    aggregator, tags = _get_aggregator_and_update_tags(key, tags)
    if aggregator is not None:
        aggregator.add("s", key, value, unit, tags, timestamp)


def gauge(
    key,  # type: str
    value,  # type: float
    unit="none",  # type: MetricValue
    tags=None,  # type: Optional[MetricTags]
    timestamp=None,  # type: Optional[float]
):
    # type: (...) -> None
    """Emits a gauge."""
    aggregator, tags = _get_aggregator_and_update_tags(key, tags)
    if aggregator is not None:
        aggregator.add("g", key, value, unit, tags, timestamp)
