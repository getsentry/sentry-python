import contextlib
import os
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from textwrap import dedent
from unittest import mock

import pytest

import sentry_sdk
from sentry_sdk import (
    Hub,
    Client,
    add_breadcrumb,
    configure_scope,
    capture_message,
    capture_exception,
    capture_event,
    set_tag,
    start_transaction,
)
from sentry_sdk.spotlight import DEFAULT_SPOTLIGHT_URL
from sentry_sdk.utils import capture_internal_exception
from sentry_sdk.integrations.executing import ExecutingIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.serializer import MAX_DATABAG_BREADTH
from sentry_sdk.consts import DEFAULT_MAX_BREADCRUMBS, DEFAULT_MAX_VALUE_LENGTH

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Optional, Union
    from sentry_sdk._types import Event


maximum_python_312 = pytest.mark.skipif(
    sys.version_info > (3, 12),
    reason="Since Python 3.13, `FrameLocalsProxy` skips items of `locals()` that have non-`str` keys; this is a CPython implementation detail: https://github.com/python/cpython/blame/7b413952e817ae87bfda2ac85dd84d30a6ce743b/Objects/frameobject.c#L148",
)


class EnvelopeCapturedError(Exception):
    pass


class _TestTransport(Transport):
    def capture_envelope(self, envelope):
        raise EnvelopeCapturedError(envelope)


def test_databag_depth_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    value = ["a"]
    for _ in range(100000):
        value = [value]

    @benchmark
    def inner():
        del events[:]
        try:
            a = value  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert len(json.dumps(event)) < 10000


def test_databag_string_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    @benchmark
    def inner():
        del events[:]
        try:
            a = "A" * DEFAULT_MAX_VALUE_LENGTH * 10  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert len(json.dumps(event)) < DEFAULT_MAX_VALUE_LENGTH * 10


def test_databag_breadth_stripping(sentry_init, capture_events, benchmark):
    sentry_init()
    events = capture_events()

    @benchmark
    def inner():
        del events[:]
        try:
            a = ["a"] * 1000000  # noqa
            1 / 0
        except Exception:
            capture_exception()

        (event,) = events

        assert (
            len(event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]["a"])
            == MAX_DATABAG_BREADTH
        )
        assert len(json.dumps(event)) < 10000
