"""saathi/call/match.py — the corpus the thresholds were set from, and
the three bands. DECISIONS.md 2026-09-25 records the numbers."""

import pytest

from saathi.call.match import (
    CONFIDENT,
    UNSURE,
    fold,
    jaro_winkler,
    match,
    normalise,
    similarity,
)

SAME_PERSON = [
    ("Basudeb", "Basudev"),
    ("Basudeb", "Vasudev"),
    ("Vasudev", "Basudev"),
    ("Zhang Wei", "Wei Zhang"),
    ("zhangwei", "Zhang Wei"),
    ("Zhāng Wěi", "Zhang Wei"),
    ("zhang1 wei4", "Zhang Wei"),
    ("Priya", "Pria"),
    ("Priya", "Priyah"),
    ("Siddharth", "Sidharth"),
    ("Siddharth", "Sidhart"),
    ("Shyam", "Syam"),
    ("Deepak", "Dipak"),
    ("Pooja", "Puja"),
    ("Xiao Ming", "Siao Ming"),
    ("Qing", "Ching"),
    ("Li Na", "Na Li"),
    ("Chen Jing", "Chen Jin"),
    ("Lakshmi", "Laxmi"),
    ("José", "Jose"),
    ("Thomas", "Tomas"),
    ("Philip", "Filip"),
    ("Priya", "Priya Sharma"),
    ("Ravi", "Rabi"),
]

DIFFERENT_PEOPLE = [
    ("Basudeb", "Priya"),
    ("Priya", "Ravi"),
    ("Zhang Wei", "Li Na"),
    ("Ravi", "Rajesh"),
    ("Rahul", "Rakesh"),
    ("Wei", "Li"),
    ("Anil", "Sunil"),
    ("Amit", "Sumit"),
    ("Ramesh", "Suresh"),
    ("Asha", "Usha"),
    ("Zhou", "Chou"),
]

# Close enough that she must be asked, not dialled and not refused.
ASK_HER = [
    ("Anand", "Anant"),
    ("Priya", "Piya"),
    ("Meena", "Maya"),
    ("Mohammed", "Muhammad"),
    ("John", "Joan"),
    ("Wong", "Wang"),
]


def test_the_corpus_has_at_least_thirty_pairs():
    assert len(SAME_PERSON) + len(DIFFERENT_PEOPLE) + len(ASK_HER) >= 30


@pytest.mark.parametrize("said, saved", SAME_PERSON)
def test_same_person_scores_confident(said, saved):
    assert similarity(said, saved) >= CONFIDENT


@pytest.mark.parametrize("said, saved", DIFFERENT_PEOPLE)
def test_different_people_fall_below_unsure(said, saved):
    assert similarity(said, saved) < UNSURE


@pytest.mark.parametrize("said, saved", ASK_HER)
def test_ambiguous_pairs_land_between(said, saved):
    assert UNSURE <= similarity(said, saved) < CONFIDENT


def test_similarity_is_symmetric_on_the_corpus():
    for a, b in SAME_PERSON + DIFFERENT_PEOPLE + ASK_HER:
        if len(normalise(a)) == len(normalise(b)):
            assert abs(similarity(a, b) - similarity(b, a)) < 1e-9


def test_folding_table():
    assert fold("vasudev") == fold("basudeb") == "basudeb"
    assert fold("zhang") == "zan"
    assert fold("shyam") == fold("syam")
    assert fold("xiao") == fold("siao")
    assert fold("qing") == fold("ching")
    assert normalise("Zhāng-Wěi3") == ["zhang", "wei"]


def test_jaro_winkler_reference_values():
    # Classic worked examples from Winkler (1990).
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.961, abs=1e-3)
    assert jaro_winkler("dwayne", "duane") == pytest.approx(0.84, abs=1e-3)
    assert jaro_winkler("dixon", "dicksonx") == pytest.approx(0.813, abs=1e-3)
    assert jaro_winkler("", "x") == 0.0 and jaro_winkler("abc", "abc") == 1.0


def test_confident_band_picks_the_one_person():
    result = match("Vasudev", ["Basudeb", "Priya", "Zhang Wei"])
    assert result.band == "confident" and result.best == "Basudeb"


def test_two_close_names_are_unsure_not_confident():
    result = match("Meena", ["Meena", "Mina", "Ravi"])
    assert result.band == "unsure"
    assert set(result.choices()) == {"Meena", "Mina"}


def test_a_confident_score_without_daylight_to_the_runner_up_is_unsure():
    result = match("Basudeb", ["Basudev", "Vasudev"])  # two saved spellings, two people
    assert result.band == "unsure" and len(result.choices()) == 2


def test_unsure_offers_at_most_three():
    result = match("Mina", ["Meena", "Mina", "Minah", "Meenah", "Ravi"])
    assert result.band == "unsure" and len(result.choices()) == 3


def test_one_plausible_but_not_confident_name_is_still_unsure():
    result = match("Anant", ["Anand", "Ravi"])
    assert result.band == "unsure" and result.choices() == ["Anand"]


def test_no_plausible_name_is_none():
    result = match("Suresh", ["Priya", "Basudeb"])
    assert result.band == "none" and result.best is None
    assert match("Priya", []).band == "none"
