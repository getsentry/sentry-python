from sentry_sdk.flag import Flag, FlagManager


def test_serializing_flag():
    flag = Flag("k", True)
    assert flag.as_dict == {"flag": "k", "result": True}


def test_flag_manager():
    manager = FlagManager(capacity=2)

    # Partially filled buffer serializes in the correct order.
    manager.add("a", True)
    flags = manager.serialize()
    assert flags == [{"flag": "a", "result": True}]

    # Filled buffer serializes in the correct order.
    manager.add("b", False)
    flags = manager.serialize()
    assert flags == [{"flag": "a", "result": True}, {"flag": "b", "result": False}]

    # Over-filled buffer serializes in the correct order.
    manager.add("c", True)
    flags = manager.serialize()
    assert flags == [{"flag": "b", "result": False}, {"flag": "c", "result": True}]

    # Twice-filled buffer serializes in the correct order.
    manager.add("d", True)
    flags = manager.serialize()
    assert flags == [{"flag": "c", "result": True}, {"flag": "d", "result": True}]
