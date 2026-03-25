import sys
import pytest

from sentry_sdk.utils import event_from_exception


try:
    # Python 3.11
    from builtins import ExceptionGroup  # type: ignore
except ImportError:
    # Python 3.10 and below
    ExceptionGroup = None


minimum_python_311 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="ExceptionGroup tests need Python >= 3.11"
)


@minimum_python_311
def test_exceptiongroup():
    exception_group = None

    try:
        try:
            raise RuntimeError("something")
        except RuntimeError:
            raise ExceptionGroup(
                "nested",
                [
                    ValueError(654),
                    ExceptionGroup(
                        "imports",
                        [
                            ImportError("no_such_module"),
                            ModuleNotFoundError("another_module"),
                        ],
                    ),
                    TypeError("int"),
                ],
            )
    except ExceptionGroup as e:
        exception_group = e

    (event, _) = event_from_exception(
        exception_group,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    values = event["exception"]["values"]

    # For this test the stacktrace and the module is not important
    for x in values:
        if "stacktrace" in x:
            del x["stacktrace"]
        if "module" in x:
            del x["module"]

    expected_values = [
        {
            "mechanism": {
                "exception_id": 6,
                "handled": False,
                "parent_id": 0,
                "source": "exceptions[2]",
                "type": "chained",
            },
            "type": "TypeError",
            "value": "int",
        },
        {
            "mechanism": {
                "exception_id": 5,
                "handled": False,
                "parent_id": 3,
                "source": "exceptions[1]",
                "type": "chained",
            },
            "type": "ModuleNotFoundError",
            "value": "another_module",
        },
        {
            "mechanism": {
                "exception_id": 4,
                "handled": False,
                "parent_id": 3,
                "source": "exceptions[0]",
                "type": "chained",
            },
            "type": "ImportError",
            "value": "no_such_module",
        },
        {
            "mechanism": {
                "exception_id": 3,
                "handled": False,
                "is_exception_group": True,
                "parent_id": 0,
                "source": "exceptions[1]",
                "type": "chained",
            },
            "type": "ExceptionGroup",
            "value": "imports",
        },
        {
            "mechanism": {
                "exception_id": 2,
                "handled": False,
                "parent_id": 0,
                "source": "exceptions[0]",
                "type": "chained",
            },
            "type": "ValueError",
            "value": "654",
        },
        {
            "mechanism": {
                "exception_id": 1,
                "handled": False,
                "parent_id": 0,
                "source": "__context__",
                "type": "chained",
            },
            "type": "RuntimeError",
            "value": "something",
        },
        {
            "mechanism": {
                "exception_id": 0,
                "handled": False,
                "is_exception_group": True,
                "type": "test_suite",
            },
            "type": "ExceptionGroup",
            "value": "nested",
        },
    ]

    assert values == expected_values


@minimum_python_311
def test_exceptiongroup_simple():
    exception_group = None

    try:
        raise ExceptionGroup(
            "simple",
            [
                RuntimeError("something strange's going on"),
            ],
        )
    except ExceptionGroup as e:
        exception_group = e

    (event, _) = event_from_exception(
        exception_group,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    exception_values = event["exception"]["values"]

    assert len(exception_values) == 2

    assert exception_values[0]["type"] == "RuntimeError"
    assert exception_values[0]["value"] == "something strange's going on"
    assert exception_values[0]["mechanism"] == {
        "type": "chained",
        "handled": False,
        "exception_id": 1,
        "source": "exceptions[0]",
        "parent_id": 0,
    }

    assert exception_values[1]["type"] == "ExceptionGroup"
    assert exception_values[1]["value"] == "simple"
    assert exception_values[1]["mechanism"] == {
        "type": "test_suite",
        "handled": False,
        "exception_id": 0,
        "is_exception_group": True,
    }
    frame = exception_values[1]["stacktrace"]["frames"][0]
    assert frame["module"] == "tests.test_exceptiongroup"
    assert frame["context_line"] == "        raise ExceptionGroup("


@minimum_python_311
def test_exception_chain_cause():
    exception_chain_cause = ValueError("Exception with cause")
    exception_chain_cause.__context__ = TypeError("Exception in __context__")
    exception_chain_cause.__cause__ = TypeError(
        "Exception in __cause__"
    )  # this implicitly sets exception_chain_cause.__suppress_context__=True

    (event, _) = event_from_exception(
        exception_chain_cause,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    expected_exception_values = [
        {
            "mechanism": {
                "handled": False,
                "type": "test_suite",
            },
            "module": None,
            "type": "TypeError",
            "value": "Exception in __cause__",
        },
        {
            "mechanism": {
                "handled": False,
                "type": "test_suite",
            },
            "module": None,
            "type": "ValueError",
            "value": "Exception with cause",
        },
    ]

    exception_values = event["exception"]["values"]
    assert exception_values == expected_exception_values


@minimum_python_311
def test_exception_chain_context():
    exception_chain_context = ValueError("Exception with context")
    exception_chain_context.__context__ = TypeError("Exception in __context__")

    (event, _) = event_from_exception(
        exception_chain_context,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    expected_exception_values = [
        {
            "mechanism": {
                "handled": False,
                "type": "test_suite",
            },
            "module": None,
            "type": "TypeError",
            "value": "Exception in __context__",
        },
        {
            "mechanism": {
                "handled": False,
                "type": "test_suite",
            },
            "module": None,
            "type": "ValueError",
            "value": "Exception with context",
        },
    ]

    exception_values = event["exception"]["values"]
    assert exception_values == expected_exception_values


@minimum_python_311
def test_simple_exception():
    simple_excpetion = ValueError("A simple exception")

    (event, _) = event_from_exception(
        simple_excpetion,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    expected_exception_values = [
        {
            "mechanism": {
                "handled": False,
                "type": "test_suite",
            },
            "module": None,
            "type": "ValueError",
            "value": "A simple exception",
        },
    ]

    exception_values = event["exception"]["values"]
    assert exception_values == expected_exception_values


@minimum_python_311
def test_cyclic_exception_group_cause():
    """
    Regression test for https://github.com/getsentry/sentry-python/issues/5025

    When using FastAPI with multiple BaseHTTPMiddleware instances, anyio wraps
    exceptions in ExceptionGroups.

    When a single exception is unpacked from the ExceptionGroup by Starlette and reraised in the `collapse_excgroups()` method
    (see https://github.com/Kludex/starlette/blob/0e88e92b592bfa11fd92e331869a8d49ba34b541/starlette/_utils.py#L79-L87)
    it causes the following situation to happen:

        ExceptionGroup -> .exceptions[0] -> ValueError -> __cause__ -> ExceptionGroup

    when the Sentry SDK attempts to walk through the chain of exceptions via the exceptions_from_error().

    This is because `exceptions_from_error` walks both __cause__/__context__ and
    ExceptionGroup.exceptions recursively without cycle detection, causing
    infinite recursion and a silent RecursionError that drops the event.
    """
    # Construct the exact cyclic structure that anyio/Starlette creates when
    # an exception propagates through multiple BaseHTTPMiddleware layers.
    original = ValueError("original error")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [original])
    original.__cause__ = group
    original.__suppress_context__ = True

    # When the ExceptionGroup is the top-level exception, exceptions_from_error
    # is called directly (not walk_exception_chain which has cycle detection).
    (event, _) = event_from_exception(
        group,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    exception_values = event["exception"]["values"]

    # Must produce a finite list of exceptions without hitting RecursionError.
    assert len(exception_values) >= 1
    exc_types = [v["type"] for v in exception_values]
    assert "ExceptionGroup" in exc_types
    assert "ValueError" in exc_types


@minimum_python_311
def test_cyclic_exception_group_context():
    """
    Same as test_cyclic_exception_group_cause (see above) but the cycle goes through
    __context__ instead of __cause__ .

    This is the more likely scenario to occur in production because __context__ is set implicitly by
    Python while __cause__ is set explicitly when "raise X from Y" is written in the code
    """
    original = ValueError("original error")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [original])
    original.__context__ = group
    # __suppress_context__ = False so that exceptions_from_error follows __context__
    original.__suppress_context__ = False

    (event, _) = event_from_exception(
        group,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    exception_values = event["exception"]["values"]
    assert len(exception_values) >= 1
    exc_types = [v["type"] for v in exception_values]
    assert "ExceptionGroup" in exc_types
    assert "ValueError" in exc_types


@minimum_python_311
def test_deeply_nested_cyclic_exception_group():
    """
    A more complex cycle: ExceptionGroup -> ValueError -> __cause__ ->
    ExceptionGroup (nested) -> TypeError -> __cause__ -> original ExceptionGroup
    """
    inner_error = TypeError("inner")
    outer_error = ValueError("outer")
    inner_group = ExceptionGroup("inner group", [inner_error])
    outer_group = ExceptionGroup("outer group", [outer_error])

    # Create a cycle spanning two ExceptionGroups
    outer_error.__cause__ = inner_group
    outer_error.__suppress_context__ = True
    inner_error.__cause__ = outer_group
    inner_error.__suppress_context__ = True

    (event, _) = event_from_exception(
        outer_group,
        client_options={
            "include_local_variables": True,
            "include_source_context": True,
            "max_value_length": 1024,
        },
        mechanism={"type": "test_suite", "handled": False},
    )

    exception_values = event["exception"]["values"]
    assert len(exception_values) >= 1
    exc_types = [v["type"] for v in exception_values]
    assert "ExceptionGroup" in exc_types
    assert "ValueError" in exc_types
    assert "TypeError" in exc_types
