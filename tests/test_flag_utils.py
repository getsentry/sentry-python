from sentry_sdk.flag_utils import FlagBuffer


def test_flag_tracking():
    """Assert the ring buffer works."""
    buffer = FlagBuffer(capacity=3)
    buffer.set("a", True)
    flags = buffer.get()
    assert len(flags) == 1
    assert flags == [{"flag": "a", "result": True}]

    buffer.set("b", True)
    flags = buffer.get()
    assert len(flags) == 2
    assert flags == [{"flag": "a", "result": True}, {"flag": "b", "result": True}]

    buffer.set("c", True)
    flags = buffer.get()
    assert len(flags) == 3
    assert flags == [
        {"flag": "a", "result": True},
        {"flag": "b", "result": True},
        {"flag": "c", "result": True},
    ]

    buffer.set("d", False)
    flags = buffer.get()
    assert len(flags) == 3
    assert flags == [
        {"flag": "b", "result": True},
        {"flag": "c", "result": True},
        {"flag": "d", "result": False},
    ]

    buffer.set("e", False)
    buffer.set("f", False)
    flags = buffer.get()
    assert len(flags) == 3
    assert flags == [
        {"flag": "d", "result": False},
        {"flag": "e", "result": False},
        {"flag": "f", "result": False},
    ]
