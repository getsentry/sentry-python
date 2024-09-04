from sentry_sdk.flag import Flag, FlagManager


def test_calling_flag():
    flag = Flag("k", True)
    last_call_time = flag.last_call_time

    flag.called(False)
    assert flag.name == "k"
    assert flag.last_result is False
    assert flag.last_call_time > last_call_time
    assert flag.call_count == 2

    flag.called(False)
    assert flag.call_count == 3


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
