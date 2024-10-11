# import asyncio
# import pytest
# import concurrent.futures as cf

from sentry_sdk.flag_utils import get_flags, set_flag


def test_flag_tracking():
    """Assert the ring buffer works."""
    set_flag("a", True)
    flags = get_flags()
    assert len(flags) == 1
    assert flags == [{"flag": "a", "result": True}]

    set_flag("b", True)
    flags = get_flags()
    assert len(flags) == 2
    assert flags == [{"flag": "a", "result": True}, {"flag": "b", "result": True}]

    set_flag("c", True)
    flags = get_flags()
    assert len(flags) == 3
    assert flags == [
        {"flag": "a", "result": True},
        {"flag": "b", "result": True},
        {"flag": "c", "result": True},
    ]

    set_flag("d", False)
    flags = get_flags()
    assert len(flags) == 3
    assert flags == [
        {"flag": "b", "result": True},
        {"flag": "c", "result": True},
        {"flag": "d", "result": False},
    ]

    set_flag("e", False)
    set_flag("f", False)
    flags = get_flags()
    assert len(flags) == 3
    assert flags == [
        {"flag": "d", "result": False},
        {"flag": "e", "result": False},
        {"flag": "f", "result": False},
    ]


# Not applicable right now. Thread-specific testing might be moved to another
# module depending on who eventually managees it.


# def test_flag_manager_asyncio_isolation(i):
#     """Assert concurrently evaluated flags do not pollute one another."""

#     async def task(chars: str):
#         for char in chars:
#             set_flag(char, True)
#         return [f["flag"] for f in get_flags()]

#     async def runner():
#         return asyncio.gather(
#             task("abc"),
#             task("de"),
#             task("fghijk"),
#         )

#     results = asyncio.run(runner()).result()

#     assert results[0] == ["a", "b", "c"]
#     assert results[1] == ["d", "e"]
#     assert results[2] == ["i", "j", "k"]


# def test_flag_manager_thread_isolation(i):
#     """Assert concurrently evaluated flags do not pollute one another."""

#     def task(chars: str):
#         for char in chars:
#             set_flag(char, True)
#         return [f["flag"] for f in get_flags()]

#     with cf.ThreadPoolExecutor(max_workers=3) as pool:
#         results = list(pool.map(task, ["abc", "de", "fghijk"]))

#     assert results[0] == ["a", "b", "c"]
#     assert results[1] == ["d", "e"]
#     assert results[2] == ["i", "j", "k"]
