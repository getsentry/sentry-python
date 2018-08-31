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

    def send_event(self, event):
        print("capture event was called")
        print(event)

    transport.HttpTransport._send_event = send_event

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
    assert b"capture event was called" in output
