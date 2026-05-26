import faulthandler

import pytest


@pytest.fixture(autouse=True)
def _dump_child_stacks_on_hang():
    """
    Arm faulthandler inside the (pytest-forked) child process so that if a
    test wedges, the child dumps all of its thread stacks to stderr and exits
    instead of stalling pytest-forked's waitpid in the parent. Cancels on
    successful teardown so passing tests behave normally.
    """
    faulthandler.dump_traceback_later(45, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()
