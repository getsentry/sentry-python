import pytest
import sys
import subprocess

from textwrap import dedent


TEST_PARAMETERS = [
    ("", "HttpTransport"),
    ('_experiments={"transport_http2": True}', "Http2Transport"),
]

minimum_python_38 = pytest.mark.skipif(
    sys.version_info < (3, 8),
    reason="The unraisable exception hook is only available in Python 3.8 and above.",
)


@minimum_python_38
@pytest.mark.parametrize("options, transport", TEST_PARAMETERS)
def test_unraisablehook(tmpdir, options, transport):
    app = tmpdir.join("app.py")
    app.write(
        dedent(
            """
    from sentry_sdk import init, transport
    from sentry_sdk.integrations.unraisablehook import UnraisablehookIntegration

    class Undeletable:
        def __del__(self):
            1 / 0

    def capture_envelope(self, envelope):
        print("capture_envelope was called")
        event = envelope.get_event()
        if event is not None:
            print(event)

    transport.{transport}.capture_envelope = capture_envelope

    init("http://foobar@localhost/123", integrations=[UnraisablehookIntegration()], {options})

    undeletable = Undeletable()
    del undeletable
    """.format(transport=transport, options=options)
        )
    )

    output = subprocess.check_output(
        [sys.executable, str(app)], stderr=subprocess.STDOUT
    )

    assert b"ZeroDivisionError" in output
    assert b"capture_envelope was called" in output
