from dataclasses import asdict, dataclass
from typing import Optional, List

from sentry_sdk.tracing_utils import _should_be_included
import pytest


def id_function(val):
    # type: (object) -> str
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
def test_should_be_included(test_case, expected):
    # type: (ShouldBeIncludedTestCase, bool) -> None
    """Checking logic, see: https://github.com/getsentry/sentry-python/issues/3312"""
    kwargs = asdict(test_case)
    kwargs.pop("id")
    assert _should_be_included(**kwargs) == expected
