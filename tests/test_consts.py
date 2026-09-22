"""Regression guards for the GENAIOPERATION enum (issue #6416).

These tests pin the exact wire values of the ``gen_ai.operation.name`` span
attribute. Renaming or revaluing an enum member would silently change what
Sentry receives, so any change here must be an intentional protocol change.
"""

import json

import pytest

from sentry_sdk.consts import GENAIOPERATION

EXPECTED_WIRE_VALUES = {
    "CHAT": "chat",
    "CREATE_AGENT": "create_agent",
    "EMBEDDINGS": "embeddings",
    "EXECUTE_TOOL": "execute_tool",
    "HANDOFF": "handoff",
    "INVOKE_AGENT": "invoke_agent",
    "RESPONSES": "responses",
    "TEXT_COMPLETION": "text_completion",
}


def test_genaioperation_members_are_exact():
    assert {m.name: m.value for m in GENAIOPERATION} == EXPECTED_WIRE_VALUES


@pytest.mark.parametrize("member, wire_value", sorted(EXPECTED_WIRE_VALUES.items()))
def test_genaioperation_wire_value(member, wire_value):
    enum_member = GENAIOPERATION[member]
    # The attribute value sent on the wire must be byte-identical to the
    # plain string it replaced.
    assert enum_member == wire_value
    assert isinstance(enum_member, str)
    assert str(enum_member) == wire_value
    assert f"{enum_member} model" == f"{wire_value} model"
    assert json.dumps({"gen_ai.operation.name": enum_member}) == json.dumps(
        {"gen_ai.operation.name": wire_value}
    )
