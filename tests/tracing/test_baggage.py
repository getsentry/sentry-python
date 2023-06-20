# coding: utf-8
from sentry_sdk.tracing_utils import Baggage


def test_third_party_baggage():
    header = "other-vendor-value-1=foo;bar;baz, other-vendor-value-2=foo;bar;"
    baggage = Baggage.from_incoming_header(header)

    assert baggage.mutable
    assert baggage.sentry_items == {}
    assert sorted(baggage.third_party_items.split(",")) == sorted(
        "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;".split(",")
    )

    assert baggage.dynamic_sampling_context() == {}
    assert baggage.serialize() == ""
    assert sorted(baggage.serialize(include_third_party=True).split(",")) == sorted(
        "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;".split(",")
    )


def test_mixed_baggage():
    header = (
        "other-vendor-value-1=foo;bar;baz, sentry-trace_id=771a43a4192642f0b136d5159a501700, "
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3, sentry-sample_rate=0.01337, "
        "sentry-user_id=Am%C3%A9lie, sentry-foo=bar, other-vendor-value-2=foo;bar;"
    )

    baggage = Baggage.from_incoming_header(header)

    assert not baggage.mutable

    assert baggage.sentry_items == {
        "public_key": "49d0f7386ad645858ae85020e393bef3",
        "trace_id": "771a43a4192642f0b136d5159a501700",
        "user_id": "Amélie",
        "sample_rate": "0.01337",
        "foo": "bar",
    }

    assert (
        baggage.third_party_items
        == "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;"
    )

    assert baggage.dynamic_sampling_context() == {
        "public_key": "49d0f7386ad645858ae85020e393bef3",
        "trace_id": "771a43a4192642f0b136d5159a501700",
        "user_id": "Amélie",
        "sample_rate": "0.01337",
        "foo": "bar",
    }

    assert sorted(baggage.serialize().split(",")) == sorted(
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
            "sentry-sample_rate=0.01337,sentry-user_id=Am%C3%A9lie,"
            "sentry-foo=bar"
        ).split(",")
    )

    assert sorted(baggage.serialize(include_third_party=True).split(",")) == sorted(
        (
            "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
            "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
            "sentry-sample_rate=0.01337,sentry-user_id=Am%C3%A9lie,sentry-foo=bar,"
            "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;"
        ).split(",")
    )


def test_malformed_baggage():
    header = ","

    baggage = Baggage.from_incoming_header(header)

    assert baggage.sentry_items == {}
    assert baggage.third_party_items == ""
    assert baggage.mutable
