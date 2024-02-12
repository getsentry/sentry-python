import pytest

from sentry_sdk.utils import UniversalLock, create_universal_lock

try:
    import asyncio
except ImportError:
    asyncio = None


requires_asyncio = pytest.mark.skipif(asyncio is None, reason="asyncio not enabled")

global_test_var = {
    "val": 0,
}

lock = create_universal_lock()


async def _modify_global_async():
    global global_test_var
    for _ in range(100000):
        with UniversalLock(lock):
            old_val = global_test_var["val"]
            global_test_var["val"] = old_val + 1


# TODO: this test does not fail without the lock.
@pytest.mark.forked
@pytest.mark.asyncio
@requires_asyncio
async def test_universal_lock_asyncio():
    tasks = [_modify_global_async() for _ in range(10)]
    await asyncio.gather(*tasks)

    assert global_test_var["val"] == 100000 * 10
