from sentry_sdk.tracing_utils import Baggage


def test_third_party_baggage():
    header = "other-vendor-value-1=foo;bar;baz, other-vendor-value-2=foo;bar;"
    baggage = Baggage.from_incoming_header(header)

    assert baggage.mutable
    assert baggage.sentry_items == {}
    assert (
        baggage.third_party_items
        == "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;"
    )

    assert baggage.dynamic_sampling_context() == {}
    assert baggage.serialize() == ""
    assert (
        baggage.serialize(include_third_party=True)
        == "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;"
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

    assert baggage.serialize() == (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=0.01337,sentry-user_id=Am%C3%A9lie,"
        "sentry-foo=bar"
    )

    assert baggage.serialize(include_third_party=True) == (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=0.01337,sentry-user_id=Am%C3%A9lie,sentry-foo=bar,"
        "other-vendor-value-1=foo;bar;baz,other-vendor-value-2=foo;bar;"
    )


def test_sentry_only_baggage():
    header = (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=0.01337"
    )
    baggage = Baggage.from_incoming_header(header)

    assert baggage.third_party_items == ""

    serialized = baggage.serialize(include_third_party=True)

    # with no third-party items, include_third_party should change nothing
    assert serialized == baggage.serialize()
    assert serialized == (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,"
        "sentry-public_key=49d0f7386ad645858ae85020e393bef3,"
        "sentry-sample_rate=0.01337"
    )


def test_malformed_baggage():
    header = ","

    baggage = Baggage.from_incoming_header(header)

    assert baggage.sentry_items == {}
    assert baggage.third_party_items == ""
    assert baggage.mutable


def test_baggage_with_optional_whitespace_around_equals():
    # W3C baggage allows optional whitespace around "=" in list members
    header = (
        "sentry-trace_id = 771a43a4192642f0b136d5159a501700, "
        "sentry-sample_rand= 0.5, other-vendor = foo"
    )

    baggage = Baggage.from_incoming_header(header)

    assert baggage.sentry_items == {
        "trace_id": "771a43a4192642f0b136d5159a501700",
        "sample_rand": "0.5",
    }
    assert baggage._sample_rand() == 0.5
    assert baggage.serialize() == (
        "sentry-trace_id=771a43a4192642f0b136d5159a501700,sentry-sample_rand=0.5"
    )
