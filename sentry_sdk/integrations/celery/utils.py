import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import Any, Tuple
    from sentry_sdk._types import MonitorConfigScheduleUnit


def _now_seconds_since_epoch() -> float:
    # We cannot use `time.perf_counter()` when dealing with the duration
    # of a Celery task, because the start of a Celery task and
    # the end are recorded in different processes.
    # Start happens in the Celery Beat process,
    # the end in a Celery Worker process.
    return time.time()


def _get_humanized_interval(seconds: float) -> "Tuple[int, MonitorConfigScheduleUnit]":
    TIME_UNITS = (  # noqa: N806
        ("day", 60 * 60 * 24.0),
        ("hour", 60 * 60.0),
        ("minute", 60.0),
    )

    seconds = float(seconds)
    for unit, divider in TIME_UNITS:
        if seconds >= divider:
            interval = int(seconds / divider)
            return (interval, cast("MonitorConfigScheduleUnit", unit))

    return (int(seconds), "second")


def _get_driver_type_from_url_scheme(scheme) -> str:
    # Source: https://github.com/celery/kombu/blob/5cbdaf97131d8a3de005f1355b567b0d60224829/kombu/transport/__init__.py#L21-L48
    url_scheme_to_driver_type_mapping = {
        "amqp": "amqp",
        "amqps": "amqp",
        "confluentkafka": "kafka",
        "kafka": "kafka",
        "memory": "memory",
        "redis": "redis",
        "rediss": "redis",
        "SQS": "sqs",
        "sqs": "sqs",
        "mongodb": "mongodb",
        "zookeeper": "zookeeper",
        "filesystem": "filesystem",
        "qpid": "qpid",
        "sentinel": "redis",
        "consul": "consul",
        "etcd": "etcd",
        "pyro": "pyro",
        "gcpubsub": "gcpubsub",
    }

    # "N/A" is set as the fallback value in Kombu when a `driver_type`
    # is not set on the Kombu Transport class itself.
    return url_scheme_to_driver_type_mapping.get(scheme, "N/A")


class NoOpMgr:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: "Any", exc_value: "Any", traceback: "Any") -> None:
        return None
