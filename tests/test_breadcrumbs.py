from unittest import mock

import sentry_sdk


def test_breadcrumbs(sentry_init, capture_events):
    """
    This test illustrates how breadcrumbs are added to the error event when an error occurs
    """
    sentry_init(
        traces_sample_rate=1.0,
    )
    events = capture_events()

    add_breadcrumbs_kwargs = {
        "type": "navigation",
        "category": "unit_tests.breadcrumbs",
        "level": "fatal",
        "origin": "unit-tests",
        "data": {
            "string": "foobar",
            "number": 4.2,
            "array": [1, 2, 3],
            "dict": {"foo": "bar"},
        },
    }

    with sentry_sdk.start_span(name="trx-breadcrumbs"):
        sentry_sdk.add_breadcrumb(message="breadcrumb0", **add_breadcrumbs_kwargs)

        with sentry_sdk.start_span(name="span1", op="function"):
            sentry_sdk.add_breadcrumb(message="breadcrumb1", **add_breadcrumbs_kwargs)

            with sentry_sdk.start_span(name="span2", op="function"):
                sentry_sdk.add_breadcrumb(
                    message="breadcrumb2", **add_breadcrumbs_kwargs
                )

                with sentry_sdk.start_span(name="span3", op="function"):
                    sentry_sdk.add_breadcrumb(
                        message="breadcrumb3", **add_breadcrumbs_kwargs
                    )

                    try:
                        1 / 0
                    except ZeroDivisionError as ex:
                        sentry_sdk.capture_exception(ex)

    assert len(events) == 2
    error = events[0]

    breadcrumbs = error["breadcrumbs"]["values"]

    for crumb in breadcrumbs:
        print(crumb)

    assert len(breadcrumbs) == 4

    # Check for my custom breadcrumbs
    for i in range(0, 3):
        assert breadcrumbs[i]["message"] == f"breadcrumb{i}"
        assert breadcrumbs[i]["type"] == "navigation"
        assert breadcrumbs[i]["category"] == "unit_tests.breadcrumbs"
        assert breadcrumbs[i]["level"] == "fatal"
        assert breadcrumbs[i]["origin"] == "unit-tests"
        assert breadcrumbs[i]["data"] == {
            "string": "foobar",
            "number": 4.2,
            "array": [1, 2, 3],
            "dict": {"foo": "bar"},
        }
        assert breadcrumbs[i]["timestamp"] == mock.ANY

    # Check for custom breadcrumbs on span3
    assert breadcrumbs[3]["message"] == "breadcrumb3"
    assert breadcrumbs[3]["type"] == "navigation"
    assert breadcrumbs[3]["category"] == "unit_tests.breadcrumbs"
    assert breadcrumbs[3]["level"] == "fatal"
    assert breadcrumbs[3]["origin"] == "unit-tests"
    assert breadcrumbs[3]["data"] == {
        "string": "foobar",
        "number": 4.2,
        "array": [1, 2, 3],
        "dict": {"foo": "bar"},
    }
    assert breadcrumbs[3]["timestamp"] == mock.ANY
