import pytest
from dataclasses import asdict, dataclass
from typing import Optional, List

from sentry_sdk.tracing_utils import (
    _should_be_included,
    _should_continue_trace,
    Baggage,
)
from tests.conftest import TestTransportWithOptions


def id_function(val: object) -> str:
    if isinstance(val, ShouldBeIncludedTestCase):
        return val.id


@dataclass(frozen=True)
class ShouldBeIncludedTestCase:
    id: str
    is_sentry_sdk_frame: bool
    namespace: Optional[str] = None
    in_app_include: Optional[List[str]] = None
    in_app_exclude: Optional[List[str]] = None
    abs_path: Optional[str] = None
    project_root: Optional[str] = None


@pytest.mark.parametrize(
    "test_case, expected",
    [
        (
            ShouldBeIncludedTestCase(
                id="Frame from Sentry SDK",
                is_sentry_sdk_frame=True,
            ),
            False,
        ),
        (
            ShouldBeIncludedTestCase(
                id="Frame from Django installed in virtualenv inside project root",
                is_sentry_sdk_frame=False,
                abs_path="/home/username/some_project/.venv/lib/python3.12/site-packages/django/db/models/sql/compiler",
                project_root="/home/username/some_project",
                namespace="django.db.models.sql.compiler",
                in_app_include=["django"],
            ),
            True,
        ),
        (
            ShouldBeIncludedTestCase(
                id="Frame from project",
                is_sentry_sdk_frame=False,
                abs_path="/home/username/some_project/some_project/__init__.py",
                project_root="/home/username/some_project",
                namespace="some_project",
            ),
            True,
        ),
        (
            ShouldBeIncludedTestCase(
                id="Frame from project module in `in_app_exclude`",
                is_sentry_sdk_frame=False,
                abs_path="/home/username/some_project/some_project/exclude_me/some_module.py",
                project_root="/home/username/some_project",
                namespace="some_project.exclude_me.some_module",
                in_app_exclude=["some_project.exclude_me"],
            ),
            False,
        ),
        (
            ShouldBeIncludedTestCase(
                id="Frame from system-wide installed Django",
                is_sentry_sdk_frame=False,
                abs_path="/usr/lib/python3.12/site-packages/django/db/models/sql/compiler",
                project_root="/home/username/some_project",
                namespace="django.db.models.sql.compiler",
            ),
            False,
        ),
        (
            ShouldBeIncludedTestCase(
                id="Frame from system-wide installed Django with `django` in `in_app_include`",
                is_sentry_sdk_frame=False,
                abs_path="/usr/lib/python3.12/site-packages/django/db/models/sql/compiler",
                project_root="/home/username/some_project",
                namespace="django.db.models.sql.compiler",
                in_app_include=["django"],
            ),
            True,
        ),
    ],
    ids=id_function,
)
def test_should_be_included(
    test_case: "ShouldBeIncludedTestCase", expected: bool
) -> None:
    """Checking logic, see: https://github.com/getsentry/sentry-python/issues/3312"""
    kwargs = asdict(test_case)
    kwargs.pop("id")
    assert _should_be_included(**kwargs) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    (
        ("", ""),
        ("foo=bar", "foo=bar"),
        (" foo=bar, baz =  qux ", " foo=bar, baz =  qux "),
        ("sentry-trace_id=123", ""),
        ("  sentry-trace_id = 123  ", ""),
        ("sentry-trace_id=123,sentry-public_key=456", ""),
        ("foo=bar,sentry-trace_id=123", "foo=bar"),
        ("foo=bar,sentry-trace_id=123,baz=qux", "foo=bar,baz=qux"),
        (
            "foo=bar,sentry-trace_id=123,baz=qux,sentry-public_key=456",
            "foo=bar,baz=qux",
        ),
    ),
)
def test_strip_sentry_baggage(header, expected):
    assert Baggage.strip_sentry_baggage(header) == expected


@pytest.mark.parametrize(
    ("baggage", "expected_repr"),
    (
        (Baggage(sentry_items={}), '<Baggage "", mutable=True>'),
        (Baggage(sentry_items={}, mutable=False), '<Baggage "", mutable=False>'),
        (
            Baggage(sentry_items={"foo": "bar"}),
            '<Baggage "sentry-foo=bar,", mutable=True>',
        ),
        (
            Baggage(sentry_items={"foo": "bar"}, mutable=False),
            '<Baggage "sentry-foo=bar,", mutable=False>',
        ),
        (
            Baggage(sentry_items={"foo": "bar"}, third_party_items="asdf=1234,"),
            '<Baggage "sentry-foo=bar,asdf=1234,", mutable=True>',
        ),
        (
            Baggage(
                sentry_items={"foo": "bar"},
                third_party_items="asdf=1234,",
                mutable=False,
            ),
            '<Baggage "sentry-foo=bar,asdf=1234,", mutable=False>',
        ),
    ),
)
def test_baggage_repr(baggage, expected_repr):
    assert repr(baggage) == expected_repr


@pytest.mark.parametrize(
    (
        "baggage_header",
        "dsn",
        "explicit_org_id",
        "strict_trace_continuation",
        "should_continue_trace",
    ),
    (
        # continue cases when strict_trace_continuation=False
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            None,
            False,
            True,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            None,
            False,
            True,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            None,
            None,
            False,
            True,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            None,
            "1234",
            False,
            True,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@not_org_id.ingest.sentry.io/12312012",
            None,
            False,
            True,
        ),
        # start new cases when strict_trace_continuation=False
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o9999.ingest.sentry.io/12312012",
            None,
            False,
            False,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            "9999",
            False,
            False,
        ),
        # continue cases when strict_trace_continuation=True
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            None,
            True,
            True,
        ),
        ("sentry-trace_id=771a43a4192642f0b136d5159a501700", None, None, True, True),
        # start new cases when strict_trace_continuation=True
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            None,
            True,
            False,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            None,
            None,
            True,
            False,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@not_org_id.ingest.sentry.io/12312012",
            None,
            True,
            False,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o9999.ingest.sentry.io/12312012",
            None,
            True,
            False,
        ),
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700, sentry-org_id=1234",
            "https://mysecret@o1234.ingest.sentry.io/12312012",
            "9999",
            True,
            False,
        ),
    ),
)
def test_should_continue_trace(
    sentry_init,
    baggage_header,
    dsn,
    explicit_org_id,
    strict_trace_continuation,
    should_continue_trace,
):
    sentry_init(
        dsn=dsn,
        org_id=explicit_org_id,
        strict_trace_continuation=strict_trace_continuation,
        traces_sample_rate=1.0,
        transport=TestTransportWithOptions,
    )

    baggage = Baggage.from_incoming_header(baggage_header) if baggage_header else None
    assert _should_continue_trace(baggage) == should_continue_trace
