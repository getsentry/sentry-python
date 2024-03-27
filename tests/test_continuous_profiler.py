import time
from collections import defaultdict

import pytest

from sentry_sdk import start_transaction
from sentry_sdk.continuous_profiler import setup_continuous_profiler
from tests.test_profiler import requires_python_version

try:
    from unittest import mock  # python 3.3 and above
except ImportError:
    import mock  # python < 3.3

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
def test_continuous_profiler_invalid_mode(mode, make_options, teardown_profiling):
    with pytest.raises(ValueError):
        setup_continuous_profiler(make_options(True, mode), lambda envelope: None)


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_continuous_profiler_valid_mode(mode, make_options, teardown_profiling):
    options = make_options(True, mode)
    setup_continuous_profiler(options, lambda envelope: None)


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
def test_continuous_profiler_setup_twice(mode, make_options, teardown_profiling):
    options = make_options(True, mode)
    # setting up the first time should return True to indicate success
    assert setup_continuous_profiler(options, lambda envelope: None)
    # setting up the second time should return False to indicate no-op
    assert not setup_continuous_profiler(options, lambda envelope: None)


@requires_python_version(3, 3)
@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("thread"),
        pytest.param("gevent", marks=requires_gevent),
    ],
)
@pytest.mark.parametrize(
    "make_options",
    [pytest.param(experimental_options, id="experiment")],
)
@mock.patch("sentry_sdk.continuous_profiler.PROFILE_BUFFER_SECONDS", 0.01)
def test_continuous_profiler_enable(
    sentry_init,
    capture_envelopes,
    mode,
    make_options,
    teardown_profiling,
):
    options = make_options(True, mode)
    sentry_init(
        traces_sample_rate=1.0,
        _experiments=options.get("_experiments", {}),
    )

    envelopes = capture_envelopes()

    with start_transaction(name="profiling"):
        time.sleep(0.05)

    items = defaultdict(list)
    for envelope in envelopes:
        for item in envelope.items:
            items[item.type].append(item)

    assert len(items["transaction"]) == 1
    assert len(items["profile_chunk"]) > 0
