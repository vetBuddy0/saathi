"""saathi/call/saving.py — ask only for what's missing, read back
before saving, never silently infer a country."""

import threading

import pytest

from saathi.call import contacts
from saathi.call.cards import FakeCardController, TooManyOptions, choice, readback
from saathi.call.saving import SaveFlow
from saathi.identity.store import IdentityStore


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    cards = FakeCardController()
    learned = []
    clock = {"t": 1000.0}
    flow = SaveFlow(path, cards, lambda: "SG", on_country_confirmed=learned.append,
                    clock=lambda: clock["t"])
    return flow, cards, path, learned, clock


def test_a_complete_request_shows_a_readback_and_saves_nothing_yet(setup):
    flow, cards, path, _, _ = setup
    result = flow.propose(name="Priya", number="nine one two three four five six seven",
                          relation="my daughter")
    assert result["status"] == "readback"
    card = cards.current
    assert card.kind == "readback" and card.value == "+65 9123 4567"
    assert card.spoken.startswith("That's a Singapore number, plus six five.")
    assert "nine one two three, four five six seven" in card.spoken
    assert card.spoken in result["note"]
    assert contacts.list_contacts(path) == []


def test_yes_saves_and_learns_the_country(setup):
    flow, cards, path, learned, _ = setup
    flow.propose(name="Priya", number="nine one two three four five six seven",
                 relation="daughter")
    card_id = cards.current.id
    assert cards.answer(card_id, {"yes": True}, source="tap")
    saved = contacts.find_by_relation(path, "daughter")
    assert saved.phone == ("+" + "6591234567") and saved.name == "Priya"
    assert learned == ["SG"]
    assert flow.outcome(card_id)["status"] == "saved"


def test_no_saves_nothing_keeps_who_and_asks_only_for_the_number(setup):
    flow, cards, path, _, _ = setup
    flow.propose(name="Priya", number="nine one two three four five six seven")
    cards.answer(cards.current.id, {"yes": False}, source="voice")
    assert contacts.list_contacts(path) == []
    result = flow.propose(number="nine one two three four five six eight")
    assert result["status"] == "readback"
    assert "Priya" in cards.current.title


def test_missing_name_is_asked_for_alone(setup):
    flow, *_ = setup
    result = flow.propose(number="nine one two three four five six seven")
    assert result["missing"] == ["who"]
    assert "whose number" in result["note"] and "phone number" not in result["note"]


def test_missing_number_is_asked_for_alone(setup):
    flow, *_ = setup
    result = flow.propose(name="Priya")
    assert result["missing"] == ["number"]
    assert "phone number" in result["note"] and "whose" not in result["note"]


def test_a_pause_mid_number_across_turns_is_appended(setup):
    flow, cards, *_ = setup
    first = flow.propose(name="Priya", number="nine one two three")
    assert first["missing"] == ["more_digits"]
    second = flow.propose(number="four five six seven")
    assert second["status"] == "readback" and cards.current.value == "+65 9123 4567"


def test_restating_from_the_start_replaces_rather_than_appends(setup):
    flow, cards, *_ = setup
    flow.propose(name="Priya", number="nine one two three")
    flow.propose(number="nine one two three four five six seven")
    assert cards.current.value == "+65 9123 4567"


def test_an_explicit_country_code_is_not_announced_as_inferred(setup):
    flow, cards, *_ = setup
    flow.propose(name="Ravi", number="plus nine one nine eight seven six five four three two "
                 "one zero")
    assert not cards.current.spoken.startswith("That's")


def test_no_locale_asks_which_country(tmp_path):
    path = tmp_path / "db.sqlite3"
    flow = SaveFlow(path, FakeCardController(), lambda: None)
    result = flow.propose(name="Priya", number="nine one two three four five six seven")
    assert result["missing"] == ["country"]


def test_a_stale_draft_is_forgotten(setup):
    flow, _, _, _, clock = setup
    flow.propose(name="Priya")
    clock["t"] += 3600
    result = flow.propose(number="nine one two three four five six seven")
    assert result["missing"] == ["who"]


def test_a_tap_answer_from_another_thread_saves(setup):
    flow, cards, path, _, _ = setup
    flow.propose(name="Priya", number="nine one two three four five six seven")
    card_id = cards.current.id
    t = threading.Thread(target=cards.answer, args=(card_id, {"yes": True}))
    t.start()
    t.join(5)
    assert contacts.find_by_exact_name(path, "Priya") is not None


def test_only_the_card_on_screen_can_be_answered(setup):
    flow, cards, path, _, _ = setup
    flow.propose(name="A", number="nine one two three four five six seven")
    old = cards.current.id
    flow.propose(name="B", number="nine one two three four five six eight")
    assert not cards.answer(old, {"yes": True})
    assert contacts.list_contacts(path) == []


def test_card_builders_match_screens_contract():
    with pytest.raises(TooManyOptions):
        choice("Who?", ["a", "b", "c", "d"])
    # Rewritten at reconciliation: this used to pin the stub's invented
    # default wording. The real builder's default is "plus 6 5, ..." -- and
    # calling never relies on it: SaveFlow passes its own spoken text and
    # asks for yes/no, with the caller's grouping kept.
    card = readback("Priya's number", "+65 9123 4567", spoken="Is that right?",
                    confirm=True, group=False)
    assert card.value == "+65 9123 4567" and card.spoken == "Is that right?"
    assert card.as_message()["confirm"] is True


def test_choice_answers_are_one_based_like_screens_cards():
    cards = FakeCardController()
    seen = []
    cards.on_answer(seen.append)
    card = choice("Who?", ["Basudeb", "Vasudev"])
    cards.show(card)
    assert not cards.answer(card.id, {"choice": 0})  # regression: 0 was once "first"
    assert not cards.answer(card.id, {"choice": 3})  # one past the end
    assert seen == [] and cards.current is card
    assert cards.answer(card.id, {"choice": 1}, source="voice")
    assert seen[0].choice == 1 and seen[0].source == "voice"


def test_a_choice_payload_on_a_readback_card_is_rejected():
    cards = FakeCardController()
    card = readback("Priya's number", "+65 9123 4567")
    cards.show(card)
    assert not cards.answer(card.id, {"choice": 1})
