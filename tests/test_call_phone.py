"""saathi/call/phone.py — country inference is never silent."""

from saathi.call.numbers import parse_spoken_number
from saathi.call.phone import (
    country_sentence,
    group_for_display,
    infer_country,
    resolve,
    spoken_readback,
)


def _r(said, locale):
    return resolve(parse_spoken_number(said), locale)


def test_a_local_number_gets_the_locale_code_and_is_marked_inferred():
    r = _r("nine one two three four five six seven", "SG")
    assert r.e164 == ("+" + "6591234567") and r.country == "SG"
    assert r.country_inferred and r.complete
    assert country_sentence(r) == "That's a Singapore number, plus six five."


def test_an_explicit_plus_is_not_inferred_and_says_nothing_extra():
    r = _r("plus six five nine one two three four five six seven", None)
    assert r.e164 == ("+" + "6591234567") and not r.country_inferred
    assert country_sentence(r) == ""


def test_the_country_code_said_without_plus_is_recognised():
    r = _r("six five nine one two three four five six seven", "SG")
    assert r.e164 == ("+" + "6591234567") and not r.country_inferred


def test_nine_one_as_an_india_prefix():
    r = _r("plus nine one nine eight seven six five four three two one zero", None)
    assert r.e164 == ("+" + "919876543210") and r.country == "IN"
    r2 = _r("plus ninety-one nine eight seven six five four three two one zero", None)
    assert r2.e164 == r.e164


def test_a_trunk_zero_is_dropped():
    r = _r("oh nine eight seven six five four three two one zero", "IN")
    assert r.e164 == ("+" + "919876543210")


def test_no_locale_and_no_plus_means_the_country_is_missing():
    r = _r("nine one two three four five six seven", None)
    assert r.missing == ("country",) and r.e164 is None


def test_too_short_asks_for_more_and_too_long_asks_to_check():
    assert _r("nine one two three", "SG").missing == ("more_digits",)
    assert _r("nine one two three four five six seven eight nine", "SG").missing == (
        "check_number",
    )


def test_an_unknown_calling_code_is_accepted_only_when_said_with_plus():
    r = _r("plus four nine one five one two three four five six seven", None)
    assert r.e164 == ("+" + "49151234567") and r.country is None


def test_inference_order_stored_then_timezone_then_language():
    assert infer_country("IN", lambda: "Asia/Singapore", "english") == ("IN", "stored")
    assert infer_country(None, lambda: "Asia/Singapore", "hindi") == ("SG", "timezone")
    assert infer_country(None, lambda: None, "hindi") == ("IN", "language")
    assert infer_country(None, lambda: None, "chinese") == (None, "")
    assert infer_country(None, lambda: "Europe/Paris", "english") == (None, "")


def test_display_and_spoken_readback_use_the_same_groups():
    assert group_for_display(("+" + "6591234567")) == "+65 9123 4567"
    assert spoken_readback(("+" + "6591234567")) == (
        "plus six five, nine one two three, four five six seven"
    )
    assert group_for_display(("+" + "919876543210")) == "+91 9876 5432 10"
    # A lone trailing digit is never left on its own: 9 digits -> 4 + 3 + 2.
    assert group_for_display(("+" + "61412345678")) == "+61 4123 456 78"
