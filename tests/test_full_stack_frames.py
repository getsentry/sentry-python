import sentry_sdk


def test_full_stack_frames_default(sentry_init, capture_events):
    sentry_init()
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    def bar():
        raise Exception("This is a test exception")

    foo()

    (event,) = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]

    assert len(frames) == 2
    assert frames[-1]["function"] == "bar"
    assert frames[-2]["function"] == "foo"


def test_full_stack_frames_enabled(sentry_init, capture_events):
    sentry_init(
        add_full_stack=True,
    )
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    def bar():
        raise Exception("This is a test exception")

    foo()

    (event,) = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]

    assert len(frames) > 2
    assert frames[-1]["function"] == "bar"
    assert frames[-2]["function"] == "foo"
    assert frames[-3]["function"] == "foo"
    assert frames[-4]["function"] == "test_full_stack_frames_enabled"


def test_full_stack_frames_enabled_truncated(sentry_init, capture_events):
    sentry_init(
        add_full_stack=True,
        max_stack_frames=3,
    )
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    def bar():
        raise Exception("This is a test exception")

    foo()

    (event,) = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]

    assert len(frames) == 3
    assert frames[-1]["function"] == "bar"
    assert frames[-2]["function"] == "foo"
    assert frames[-3]["function"] == "foo"


def test_full_stack_frames_default_no_truncation_happening(sentry_init, capture_events):
    sentry_init(
        max_stack_frames=1,  # this is ignored if add_full_stack=False (which is the default)
    )
    events = capture_events()

    def foo():
        try:
            bar()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    def bar():
        raise Exception("This is a test exception")

    foo()

    (event,) = events
    frames = event["exception"]["values"][0]["stacktrace"]["frames"]

    assert len(frames) == 2
    assert frames[-1]["function"] == "bar"
    assert frames[-2]["function"] == "foo"
