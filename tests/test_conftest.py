import pytest


@pytest.mark.parametrize(
    "test_string, expected_result",
    [
        # type matches
        ("dogs are great!", True),  # full containment - beginning
        ("go, dogs, go!", True),  # full containment - middle
        ("I like dogs", True),  # full containment - end
        ("dogs", True),  # equality
        ("", False),  # reverse containment
        ("dog", False),  # reverse containment
        ("good dog!", False),  # partial overlap
        ("cats", False),  # no overlap
        # type mismatches
        (1231, False),
        (11.21, False),
        ([], False),
        ({}, False),
        (True, False),
    ],
)
def test_string_containing(
    test_string, expected_result, StringContaining  # noqa: N803
):
    assert (test_string == StringContaining("dogs")) is expected_result


@pytest.mark.parametrize(
    "test_dict, expected_result",
    [
        # type matches
        ({"dogs": "yes", "cats": "maybe", "spiders": "nope"}, True),  # full containment
        ({"dogs": "yes", "cats": "maybe"}, True),  # equality
        ({}, False),  # reverse containment
        ({"dogs": "yes"}, False),  # reverse containment
        ({"dogs": "yes", "birds": "only outside"}, False),  # partial overlap
        ({"coyotes": "from afar"}, False),  # no overlap
        # type mismatches
        ('{"dogs": "yes", "cats": "maybe"}', False),
        (1231, False),
        (11.21, False),
        ([], False),
        (True, False),
    ],
)
def test_dictionary_containing(
    test_dict, expected_result, DictionaryContaining  # noqa: N803
):
    assert (
        test_dict == DictionaryContaining({"dogs": "yes", "cats": "maybe"})
    ) is expected_result


class Animal:  # noqa: B903
    def __init__(self, name=None, age=None, description=None):
        self.name = name
        self.age = age
        self.description = description


class Dog(Animal):
    pass


class Cat(Animal):
    pass


@pytest.mark.parametrize(
    "test_obj, type_and_attrs_result, type_only_result, attrs_only_result",
    [
        # type matches
        (Dog("Maisey", 7, "silly"), True, True, True),  # full attr containment
        (Dog("Maisey", 7), True, True, True),  # type and attr equality
        (Dog(), False, True, False),  # reverse attr containment
        (Dog("Maisey"), False, True, False),  # reverse attr containment
        (Dog("Charlie", 7, "goofy"), False, True, False),  # partial attr overlap
        (Dog("Bodhi", 6, "floppy"), False, True, False),  # no attr overlap
        # type mismatches
        (Cat("Maisey", 7), False, False, True),  # attr equality
        (Cat("Piper", 1, "doglike"), False, False, False),
        ("Good girl, Maisey", False, False, False),
        ({"name": "Maisey", "age": 7}, False, False, False),
        (1231, False, False, False),
        (11.21, False, False, False),
        ([], False, False, False),
        (True, False, False, False),
    ],
)
def test_object_described_by(
    test_obj,
    type_and_attrs_result,
    type_only_result,
    attrs_only_result,
    ObjectDescribedBy,  # noqa: N803
):
    assert (
        test_obj == ObjectDescribedBy(type=Dog, attrs={"name": "Maisey", "age": 7})
    ) is type_and_attrs_result

    assert (test_obj == ObjectDescribedBy(type=Dog)) is type_only_result

    assert (
        test_obj == ObjectDescribedBy(attrs={"name": "Maisey", "age": 7})
    ) is attrs_only_result
