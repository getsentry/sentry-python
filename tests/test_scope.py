import copy
import json
import os
import subprocess
import tempfile

import pytest

from sentry_sdk import capture_exception
from sentry_sdk.scope import Scope

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3


def test_copying():
    s1 = Scope()
    s1.fingerprint = {}
    s1.set_tag("foo", "bar")

    s2 = copy.copy(s1)
    assert "foo" in s2._tags

    s1.set_tag("bam", "baz")
    assert "bam" in s1._tags
    assert "bam" not in s2._tags

    assert s1._fingerprint is s2._fingerprint


def test_merging(sentry_init, capture_events):
    sentry_init()

    s = Scope()
    s.set_user({"id": "42"})

    events = capture_events()

    capture_exception(NameError(), scope=s)

    (event,) = events
    assert event["user"] == {"id": "42"}


def test_common_args():
    s = Scope()
    s.update_from_kwargs(
        user={"id": 23},
        level="warning",
        extras={"k": "v"},
        contexts={"os": {"name": "Blafasel"}},
        tags={"x": "y"},
        fingerprint=["foo"],
    )

    s2 = Scope()
    s2.set_extra("foo", "bar")
    s2.set_tag("a", "b")
    s2.set_context("device", {"a": "b"})
    s2.update_from_scope(s)

    assert s._user == {"id": 23}
    assert s._level == "warning"
    assert s._extras == {"k": "v"}
    assert s._contexts == {"os": {"name": "Blafasel"}}
    assert s._tags == {"x": "y"}
    assert s._fingerprint == ["foo"]

    assert s._user == s2._user
    assert s._level == s2._level
    assert s._fingerprint == s2._fingerprint
    assert s2._extras == {"k": "v", "foo": "bar"}
    assert s2._tags == {"a": "b", "x": "y"}
    assert s2._contexts == {"os": {"name": "Blafasel"}, "device": {"a": "b"}}


BAGGAGE_VALUE = (
    "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
    "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
    "sentry-user_id=Am%C3%A9lie, other-vendor-value-2=foo;bar;"
)


@pytest.mark.parametrize(
    "env,excepted_value",
    [
        (
            {
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
            },
            {
                "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
            },
        ),
        (
            {
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "",
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "True",
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            {
                "sentry-trace": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "baggage": BAGGAGE_VALUE,
            },
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "no",
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            None,
        ),
        (
            {
                "SENTRY_USE_ENVIRONMENT": "True",
                "MY_OTHER_VALUE": "asdf",
                "SENTRY_RELEASE": "1.0.0",
            },
            None,
        ),
    ],
)
def test_load_trace_data_from_env(env, excepted_value):
    new_env = os.environ.copy()
    new_env.update(env)

    with mock.patch.dict(os.environ, new_env):
        s = Scope()
        incoming_trace_data = s._load_trace_data_from_env()
        assert incoming_trace_data == excepted_value


CODE_FOR_TEST_SCRIPT = """
import json
import sentry_sdk

class TestTransport(sentry_sdk.transport.HttpTransport):
    def _send_event(self, event):
        print("\\nEVENT: {}\\n".format(json.dumps(event)))

    def _send_envelope(self, envelope):
        print("\\nENVELOPE: {}\\n".format(json.dumps(envelope)))

def main():
    sentry_sdk.init(
        dsn="https://123abc@example.com/123",
        transport=TestTransport,
    )
    sentry_sdk.capture_message("Hello World!")

if __name__ == "__main__":
    main()
"""


@pytest.mark.parametrize(
    "env,excepted_trace_id",
    [
        (
            {},
            None,
        ),
        (
            {
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
            },
            "771a43a4192642f0b136d5159a501700",
        ),
        (
            {
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            None,
        ),
        (
            {
                "SENTRY_TRACE": "771a43a4192642f0b136d5159a501700-1234567890abcdef-1",
                "SENTRY_BAGGAGE": BAGGAGE_VALUE,
            },
            "771a43a4192642f0b136d5159a501700",
        ),
    ],
)
def test_propagate_trace_via_env_vars(env, excepted_trace_id):
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.devnull, "w+") as null:
            # Write python script to temp directory
            main_py = os.path.join(tmpdir, "main.py")
            with open(main_py, "w") as f:
                f.write(CODE_FOR_TEST_SCRIPT)

            # Set new environment variables
            new_env = os.environ.copy()
            new_env.update(env)

            # Call script and capture output
            with subprocess.Popen(
                ["python", main_py],
                env=new_env,
                stdout=subprocess.PIPE,
                stderr=null,
                stdin=null,
            ) as proc:

                output = proc.stdout.read().strip().decode("utf-8")
                events = []
                envelopes = []

                for line in output.splitlines():
                    if line.startswith("EVENT: "):
                        line = line[len("EVENT: ") :]
                        events.append(json.loads(line))
                    elif line.startswith("ENVELOPE: "):
                        line = line[len("ENVELOPE: ") :]
                        envelopes.append(json.loads(line))

                if excepted_trace_id:
                    assert (
                        events[0]["contexts"]["trace"]["trace_id"] == excepted_trace_id
                    )
                else:
                    # A new trace was started
                    assert (
                        events[0]["contexts"]["trace"]["trace_id"] != excepted_trace_id
                    )
