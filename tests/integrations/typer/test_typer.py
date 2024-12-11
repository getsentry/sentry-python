import subprocess
import sys
from textwrap import dedent
import pytest

from typer.testing import CliRunner

runner = CliRunner()


def test_catch_exceptions(tmpdir):
    app = tmpdir.join("app.py")

    app.write(
        dedent(
            """
    import typer
    from unittest import mock

    from sentry_sdk import init, transport
    from sentry_sdk.integrations.typer import TyperIntegration

    def capture_envelope(self, envelope):
        print("capture_envelope was called")
        event = envelope.get_event()
        if event is not None:
            print(event)

    transport.HttpTransport.capture_envelope = capture_envelope

    init("http://foobar@localhost/123", integrations=[TyperIntegration()])

    app = typer.Typer()

    @app.command()
    def test():
        print("test called")
        raise Exception("pollo")

    app()
    """
        )
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        subprocess.check_output([sys.executable, str(app)], stderr=subprocess.STDOUT)

    output = excinfo.value.output

    assert b"capture_envelope was called" in output
    assert b"test called" in output
    assert b"pollo" in output
