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
