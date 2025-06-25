import pytest
import sys
import subprocess

from textwrap import dedent


TEST_PARAMETERS = [("http2=False", "HttpTransport")]

if sys.version_info >= (3, 8):
    TEST_PARAMETERS.append(("", "Http2Transport"))


@pytest.mark.parametrize("options, transport", TEST_PARAMETERS)
def test_excepthook(tmpdir, options, transport):
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

    transport.{transport}.capture_envelope = capture_envelope

    init("http://foobar@localhost/123", {options})

    frame_value = "LOL"

    1/0
    """.format(
                transport=transport, options=options
            )
        )
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.check_output([sys.executable, str(app)], stderr=subprocess.STDOUT)

    output = excinfo.value.output
    print(output)

    assert b"ZeroDivisionError" in output
    assert b"LOL" in output
    assert b"capture_envelope was called" in output


@pytest.mark.parametrize("options, transport", TEST_PARAMETERS)
def test_always_value_excepthook(tmpdir, options, transport):
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

    transport.{transport}.capture_envelope = capture_envelope

    sys.ps1 = "always_value_test"
    init("http://foobar@localhost/123",
        integrations=[ExcepthookIntegration(always_run=True)],
        {options}
    )

    frame_value = "LOL"

    1/0
    """.format(
                transport=transport, options=options
            )
        )
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.check_output([sys.executable, str(app)], stderr=subprocess.STDOUT)

    output = excinfo.value.output
    print(output)

    assert b"ZeroDivisionError" in output
    assert b"LOL" in output
    assert b"capture_envelope was called" in output
