import faulthandler
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _dump_stacks_if_slow(request):
    # Temporary: find out what rq tests are waiting on when they stall in CI
    with tempfile.TemporaryFile("w+") as f:
        faulthandler.dump_traceback_later(8, repeat=True, file=f)
        try:
            yield
        finally:
            faulthandler.cancel_dump_traceback_later()
            f.seek(0)
            dump = f.read()
            if dump:
                tr = request.config.pluginmanager.get_plugin("terminalreporter")
                tr.write_line(f"\n=== stacks for {request.node.nodeid} ===\n{dump}")
