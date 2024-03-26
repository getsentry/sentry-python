import pytest

from sentry_sdk.continuous_profiler import setup_continuous_profiler
from tests.test_profiler import requires_python_version

try:
    import gevent
except ImportError:
    gevent = None


requires_gevent = pytest.mark.skipif(gevent is None, reason="gevent not enabled")


def experimental_options(enabled, mode=None):
    return {
        "_experiments": {
            "enable_continuous_profiling": enabled,
            "continuous_profiling_mode": mode,
        }
    }


@requires_python_version(3, 3)
@pytest.mark.parametrize("mode", [pytest.param("foo")])
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_profiler_invalid_mode(mode, make_options, teardown_profiling):
    with pytest.raises(ValueError):
        setup_continuous_profiler(make_options(True, mode))


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ]
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_profiler_valid_mode(mode, make_options, teardown_profiling):
    options = make_options(True, mode)
    setup_continuous_profiler(options)


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ]
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_profiler_setup_twice(mode, make_options, teardown_profiling):
    options = make_options(True, mode)
    # setting up the first time should return True to indicate success
    assert setup_continuous_profiler(options)
    # setting up the second time should return False to indicate no-op
    assert not setup_continuous_profiler(options)
