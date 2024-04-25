import pytest
import sys
import subprocess

from textwrap import dedent


def test_excepthook(tmpdir):
    app = tmpdir.join("app.py")
    app.write(
        dedent(
            """
    from sentry_sdk import init, transport

    def capture_envelope(self, envelope):
        print("capture_envelope was called")
        event = envelope.get_event()
        if event is not None:
            print(event)

    transport.HttpTransport.capture_envelope = capture_envelope

    init("http://foobar@localhost/123")

    frame_value = "LOL"

    1/0
    """
        )
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.check_output([sys.executable, str(app)], stderr=subprocess.STDOUT)

    output = excinfo.value.output
    print(output)

    assert b"ZeroDivisionError" in output
    assert b"LOL" in output
    assert b"capture_envelope was called" in output


def test_always_value_excepthook(tmpdir):
    app = tmpdir.join("app.py")
    app.write(
        dedent(
            """
    import sys
    from sentry_sdk import init, transport
    from sentry_sdk.integrations.excepthook import ExcepthookIntegration

    def capture_envelope(self, envelope):
        print("capture_envelope was called")
        event = envelope.get_event()
        if event is not None:
            print(event)

    transport.HttpTransport.capture_envelope = capture_envelope

    sys.ps1 = "always_value_test"
    init("http://foobar@localhost/123",
        integrations=[ExcepthookIntegration(always_run=True)]
    )

    frame_value = "LOL"

    1/0
    """
        )
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.check_output([sys.executable, str(app)], stderr=subprocess.STDOUT)

    output = excinfo.value.output
    print(output)

    assert b"ZeroDivisionError" in output
    assert b"LOL" in output
    assert b"capture_envelope was called" in output
