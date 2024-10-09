from unittest import mock

import sentry_sdk
from sentry_sdk.consts import OP


def test_breadcrumbs(sentry_init, capture_envelopes):
    """
    This test illustrates how breadcrumbs are added to the error event when an error occurs
    """
    sentry_init(
        traces_sample_rate=1.0,
    )
    envelopes = capture_envelopes()

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

    with sentry_sdk.start_transaction(name="trx-breadcrumbs"):
        sentry_sdk.add_breadcrumb(message="breadcrumb0", **add_breadcrumbs_kwargs)

        with sentry_sdk.start_span(name="span1", op="function"):
            sentry_sdk.add_breadcrumb(message="breadcrumb1", **add_breadcrumbs_kwargs)

            with sentry_sdk.start_span(name="span2", op="function"):
                sentry_sdk.add_breadcrumb(
                    message="breadcrumb2", **add_breadcrumbs_kwargs
                )

                # Spans that create breadcrumbs automatically
                with sentry_sdk.start_span(name="span3", op=OP.DB_REDIS) as span3:
                    span3.set_data("span3_data", "data on the redis span")
                    span3.set_tag("span3_tag", "tag on the redis span")

                with sentry_sdk.start_span(name="span4", op=OP.HTTP_CLIENT) as span4:
                    span4.set_data("span4_data", "data on the http.client span")
                    span4.set_tag("span4_tag", "tag on the http.client span")

                with sentry_sdk.start_span(name="span5", op=OP.SUBPROCESS) as span5:
                    span5.set_data("span5_data", "data on the subprocess span")
                    span5.set_tag("span5_tag", "tag on the subprocess span")

                with sentry_sdk.start_span(name="span6", op="function") as span6:
                    # This data on the span is not added to custom breadcrumbs.
                    # Data from the span is only added to automatic breadcrumbs shown above
                    span6.set_data("span6_data", "data on span6")
                    span6.set_tag("span6_tag", "tag on the span6")
                    sentry_sdk.add_breadcrumb(
                        message="breadcrumb6", **add_breadcrumbs_kwargs
                    )

                    try:
                        1 / 0
                    except ZeroDivisionError as ex:
                        sentry_sdk.capture_exception(ex)

    (error_envelope, transaction_envelope) = envelopes
    error = error_envelope.get_event()
    transaction = transaction_envelope.get_transaction_event()

    breadcrumbs = error["breadcrumbs"]["values"]

    for crumb in breadcrumbs:
        print(crumb)

    assert len(breadcrumbs) == 7

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

    # Check automatic redis breadcrumbs
    assert breadcrumbs[3]["message"] == f"span3"
    assert breadcrumbs[3]["type"] == "redis"
    assert breadcrumbs[3]["category"] == "redis"
    assert "level" not in breadcrumbs[3]
    assert "origin" not in breadcrumbs[3]
    assert breadcrumbs[3]["data"] == {
        "span3_tag": "tag on the redis span",
    }
    assert breadcrumbs[3]["timestamp"] == mock.ANY

    # Check automatic http.client breadcrumbs
    assert "message" not in breadcrumbs[4]
    assert breadcrumbs[4]["type"] == "http"
    assert breadcrumbs[4]["category"] == "httplib"
    assert "level" not in breadcrumbs[4]
    assert "origin" not in breadcrumbs[4]
    assert breadcrumbs[4]["data"] == {
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
        "span4_data": "data on the http.client span",
    }
    assert breadcrumbs[4]["timestamp"] == mock.ANY

    # Check automatic subprocess breadcrumbs
    assert breadcrumbs[5]["message"] == f"span5"
    assert breadcrumbs[5]["type"] == "subprocess"
    assert breadcrumbs[5]["category"] == "subprocess"
    assert "level" not in breadcrumbs[5]
    assert "origin" not in breadcrumbs[5]
    assert breadcrumbs[5]["data"] == {
        "thread.id": mock.ANY,
        "thread.name": mock.ANY,
        "span5_data": "data on the subprocess span",
    }
    assert breadcrumbs[5]["timestamp"] == mock.ANY

    # Check for custom breadcrumbs on span6
    assert breadcrumbs[6]["message"] == f"breadcrumb6"
    assert breadcrumbs[6]["type"] == "navigation"
    assert breadcrumbs[6]["category"] == "unit_tests.breadcrumbs"
    assert breadcrumbs[6]["level"] == "fatal"
    assert breadcrumbs[6]["origin"] == "unit-tests"
    assert breadcrumbs[6]["data"] == {
        "string": "foobar",
        "number": 4.2,
        "array": [1, 2, 3],
        "dict": {"foo": "bar"},
    }
    assert breadcrumbs[6]["timestamp"] == mock.ANY
