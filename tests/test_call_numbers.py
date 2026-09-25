"""saathi/call/numbers.py — spoken numbers to digits."""

import pytest

from saathi.call.numbers import parse_spoken_number, spell_digits


@pytest.mark.parametrize(
    "said, digits",
    [
        ("nine one two three four five six seven", "91234567"),
        ("nine one double seven four five six seven", "91774567"),
        ("triple oh one", "0001"),
        ("oh eight one", "081"),
        ("nought eight", "08"),
        ("nine one", "91"),
        ("ninety-one", "91"),
        ("ninety one", "91"),
        ("ninety", "90"),
        ("ninety oh", "900"),
        ("nineteen", "19"),
        ("eight hundred", "800"),
        ("five thousand", "5000"),
        ("nine one... then two three, um, four five six seven", "91234567"),
        ("9123 4567", "91234567"),
        ("91-23-45-67", "91234567"),
        ("double 7", "77"),
        ("the number is nine one two three and four five six seven", "91234567"),
        ("Nine ONE Two", "912"),
    ],
)
def test_digits(said, digits):
    assert parse_spoken_number(said).digits == digits


def test_nine_one_and_ninety_one_are_the_same_digits_not_nine_zero_one():
    assert parse_spoken_number("nine one").digits == parse_spoken_number("ninety-one").digits
    assert parse_spoken_number("ninety-one").digits != "901"


@pytest.mark.parametrize(
    "said, digits",
    [
        ("plus six five nine one two three four five six seven", "6591234567"),
        ("+65 9123 4567", "6591234567"),
        ("zero zero six five nine one two three four five six seven", "6591234567"),
    ],
)
def test_international_forms(said, digits):
    parsed = parse_spoken_number(said)
    assert parsed.international and parsed.digits == digits


def test_plus_after_digits_is_not_international():
    assert not parse_spoken_number("nine one plus").international


def test_homophones_are_unknown_not_silent_digits():
    parsed = parse_spoken_number("the number for Priya is nine one")
    assert parsed.digits == "91"
    assert "priya" in parsed.unknown
    assert parsed.confidence < 1.0


def test_nothing_numeric_means_the_number_is_missing():
    parsed = parse_spoken_number("I don't remember")
    assert parsed.digits == "" and parsed.missing == ("number",)
    assert parse_spoken_number("").missing == ("number",)


def test_a_clean_number_is_fully_confident():
    parsed = parse_spoken_number("nine one double seven")
    assert parsed.confidence == 1.0 and parsed.unknown == ()


def test_spell_digits():
    assert spell_digits("6590") == "six five nine zero"
