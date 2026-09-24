"""saathi/screen/cards.py — the four primitives and the controller that
shows one at a time. The rules under test are the brief's, each
load-bearing: three options maximum; numbered; spoken as well as shown;
one card at a time; nothing times out; always a way out; tap and voice
both answer through the same entry point.
"""

import threading

import pytest

from saathi.screen.cards import (
    Answer,
    CardController,
    HoldController,
    TooManyOptions,
    choice,
    confirm,
    group_digits,
    holding,
    readback,
    speak_value,
    validate_answer,
)


def _controller():
    sent = []
    return CardController(broadcast=sent.append), sent


# -- builders ----------------------------------------------------------------


def test_choice_numbers_its_options_and_speaks_exactly_what_is_shown():
    card = choice("Which Priya?", ["Priya, your daughter", "Priya from church"])
    assert card.kind == "choice"
    assert [(o.n, o.label) for o in card.options] == [
        (1, "Priya, your daughter"),
        (2, "Priya from church"),
    ]
    assert card.spoken == "Which Priya? One: Priya, your daughter. Two: Priya from church."
    message = card.as_message()
    assert message["options"] == [
        {"n": 1, "label": "Priya, your daughter"},
        {"n": 2, "label": "Priya from church"},
    ]
    assert message["spoken"] == card.spoken


def test_a_fourth_option_is_refused_not_truncated():
    # "More than that, say so and offer the best three" -- which three,
    # and saying so, are the caller's; silently dropping one would hide
    # both.
    with pytest.raises(TooManyOptions):
        choice("Which?", ["a", "b", "c", "d"])


def test_a_choice_needs_at_least_two_options():
    with pytest.raises(ValueError):
        choice("Which?", ["only one"])


def test_confirm_is_one_statement():
    card = confirm("Call Priya, your daughter?")
    assert card.kind == "confirm"
    assert card.title == card.spoken == "Call Priya, your daughter?"
    assert "options" not in card.as_message()


def test_readback_groups_digits_and_speaks_them_one_at_a_time():
    card = readback("Priya's number", "0412345678")
    assert card.value == "041 234 5678"
    assert card.spoken == "Priya's number 0 4 1, 2 3 4, 5 6 7 8"


def test_group_digits_handles_the_shapes_a_phone_number_takes():
    assert group_digits("0412345678") == "041 234 5678"
    assert group_digits("+61 412 345 678") == group_digits("+61412345678")
    assert group_digits("+61412345678") == "+614 123 456 78"
    assert group_digits("1234567") == "123 4567"
    assert group_digits("12") == "12"
    assert group_digits("Priya") == "Priya"
    assert speak_value("12 345") == "1 2, 3 4 5"
    assert speak_value("Priya") == "Priya"


def test_holding_clamps_progress_and_keeps_its_id_when_reissued():
    first = holding("Keep holding", -1)
    assert first.progress == 0.0
    again = holding("Keep holding", 7, card_id=first.id)
    assert again.progress == 1.0
    assert again.id == first.id


# -- one at a time, spoken, broadcast ------------------------------------


def test_show_broadcasts_the_card_and_a_second_show_replaces_the_first():
    controller, sent = _controller()
    first = choice("A?", ["x", "y"])
    second = confirm("B?")
    controller.show(first)
    controller.show(second)
    assert controller.current is second
    assert [m["card"]["id"] for m in sent] == [first.id, second.id]
    assert all(m["type"] == "card" for m in sent)


def test_a_replaced_card_is_answered_as_dismissed_so_nothing_waits_on_it():
    controller, _ = _controller()
    seen = []
    controller.on_answer(seen.append)
    first = choice("A?", ["x", "y"])
    controller.show(first)
    controller.show(confirm("B?"))
    assert len(seen) == 1
    assert seen[0].card_id == first.id
    assert seen[0].dismissed is True
    assert seen[0].source == "code"


def test_clear_broadcasts_null_and_reports_a_dismiss():
    controller, sent = _controller()
    seen = []
    controller.on_answer(seen.append)
    controller.show(confirm("B?"))
    controller.clear()
    assert controller.current is None
    assert sent[-1] == {"type": "card", "card": None}
    assert seen[-1].dismissed is True
    controller.clear()  # nothing up: still fine, still tells the screen
    assert sent[-1] == {"type": "card", "card": None}


def test_every_card_must_have_something_to_say():
    controller, _ = _controller()
    card = confirm("x")
    silent = type(card)(**{**card.__dict__, "spoken": ""})
    with pytest.raises(ValueError):
        controller.show(silent)


def test_without_a_seam_show_still_works_and_drops_the_message():
    controller = CardController()
    card = confirm("B?")
    assert controller.show(card) == card.id
    assert controller.current is card


# -- answering: tap and voice through the same door ------------------------


def test_a_tap_and_a_voice_answer_both_resolve_to_on_answer_and_clear_the_card():
    controller, sent = _controller()
    seen: list[Answer] = []
    controller.on_answer(seen.append)

    card = choice("Which?", ["a", "b", "c"])
    controller.show(card)
    assert controller.answer(card.id, {"choice": 2}, source="tap") is True
    assert controller.current is None
    assert sent[-1] == {"type": "card", "card": None}

    controller.show(card)
    assert controller.answer(card.id, {"choice": 3}, source="voice") is True

    assert [(a.choice, a.source) for a in seen] == [(2, "tap"), (3, "voice")]
    assert seen[0].card is card and seen[0].kind == "choice"


def test_a_stale_id_or_a_malformed_answer_is_dropped():
    controller, sent = _controller()
    seen = []
    controller.on_answer(seen.append)
    old = choice("Which?", ["a", "b"])
    controller.show(old)
    new = confirm("Sure?")
    controller.show(new)
    seen.clear()
    assert controller.answer(old.id, {"choice": 1}) is False  # tapped after it was replaced
    assert controller.answer(new.id, {"choice": 1}) is False  # not a shape confirm takes
    assert controller.answer(new.id, {"yes": "yes"}) is False
    assert controller.answer(new.id, "yes") is False
    assert controller.answer("nope", {"yes": True}) is False
    assert controller.current is new
    assert seen == []
    assert controller.answer(new.id, {"yes": False}) is True
    assert seen[0].yes is False


def test_validate_answer_accepts_exactly_one_shape_per_kind():
    three = choice("?", ["a", "b", "c"])
    assert validate_answer(three, {"choice": 3}) == {"choice": 3}
    assert validate_answer(three, {"choice": 4}) is None
    assert validate_answer(three, {"choice": 0}) is None
    assert validate_answer(three, {"choice": True}) is None
    assert validate_answer(three, {"choice": 2, "extra": 1}) == {"choice": 2}
    assert validate_answer(three, {"dismiss": True}) == {"dismiss": True}
    assert validate_answer(three, {"dismiss": False}) is None
    yn = confirm("?")
    assert validate_answer(yn, {"yes": True}) == {"yes": True}
    assert validate_answer(yn, {"choice": 1}) is None
    rb = readback("n", "123")
    assert validate_answer(rb, {"dismiss": True}) == {"dismiss": True}
    assert validate_answer(rb, {"yes": True}) is None
    assert validate_answer(holding("h"), {"choice": 1}) is None


def test_on_answer_can_unsubscribe():
    controller, _ = _controller()
    seen = []
    unsubscribe = controller.on_answer(seen.append)
    unsubscribe()
    card = confirm("?")
    controller.show(card)
    controller.answer(card.id, {"yes": True})
    assert seen == []


# -- ask(): blocking from another thread, no timeout by default ----------


def test_ask_blocks_the_calling_thread_until_answered_from_another():
    controller, sent = _controller()
    card = confirm("Call her?")
    result: list[Answer | None] = []

    def asker():
        result.append(controller.ask(card))

    thread = threading.Thread(target=asker)
    thread.start()
    for _ in range(200):
        if controller.current is card:
            break
        threading.Event().wait(0.005)
    assert controller.current is card
    assert sent[-1]["card"]["id"] == card.id
    assert controller.answer(card.id, {"yes": True}, source="tap") is True
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0] is not None and result[0].yes is True


def test_ask_with_a_timeout_returns_none_and_clears_the_card():
    controller, sent = _controller()
    card = confirm("Call her?")
    assert controller.ask(card, timeout=0.05) is None
    assert controller.current is None
    assert sent[-1] == {"type": "card", "card": None}


def test_ask_is_released_by_a_replacing_show():
    controller, _ = _controller()
    card = confirm("Call her?")
    result: list[Answer | None] = []
    thread = threading.Thread(target=lambda: result.append(controller.ask(card)))
    thread.start()
    for _ in range(200):
        if controller.current is card:
            break
        threading.Event().wait(0.005)
    controller.show(choice("Which?", ["a", "b"]))
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result[0] is not None and result[0].dismissed is True


async def test_ask_async_is_ask_off_the_loop():
    controller, _ = _controller()
    card = confirm("Call her?")
    import asyncio

    async def answer_soon():
        while controller.current is not card:
            await asyncio.sleep(0.005)
        controller.answer(card.id, {"yes": False}, source="voice")

    asyncio.get_running_loop().create_task(answer_soon())
    result = await controller.ask_async(card)
    assert result is not None and result.yes is False


# -- the hold seam ---------------------------------------------------------


def test_hold_is_inactive_until_a_handler_is_set_and_clear_removes_it():
    cards, _ = _controller()
    hold = HoldController(cards)
    assert hold.active is False
    hold.set_handler(lambda: None, seconds=1.5, label="Keep holding to hang up")
    assert hold.active is True
    assert hold.seconds == 1.5
    assert hold.label == "Keep holding to hang up"
    hold.clear()
    assert hold.active is False


def test_hold_shows_progress_fires_once_at_the_threshold_and_clears_the_card():
    cards, sent = _controller()
    fired = []
    hold = HoldController(cards)
    hold.set_handler(lambda: fired.append(1), seconds=2.0, label="Keep holding to hang up")

    hold.begin()
    assert hold.holding is True
    first = sent[-1]["card"]
    assert first["kind"] == "holding" and first["progress"] == 0.0
    assert first["title"] == first["spoken"] == "Keep holding to hang up"

    assert hold.tick(1.0) is False
    assert sent[-1]["card"]["progress"] == 0.5
    assert sent[-1]["card"]["id"] == first["id"]  # updated in place, not a new card
    assert fired == []

    assert hold.tick(2.0) is True
    assert fired == [1]
    assert sent[-1] == {"type": "card", "card": None}
    assert hold.holding is False
    assert hold.tick(3.0) is True  # a late tick fires nothing more
    assert fired == [1]


def test_releasing_before_the_threshold_abandons_the_hold_and_fires_nothing():
    cards, sent = _controller()
    fired = []
    hold = HoldController(cards)
    hold.set_handler(lambda: fired.append(1), seconds=2.0)
    hold.begin()
    hold.tick(0.7)
    hold.abandon()
    assert fired == []
    assert hold.holding is False
    assert sent[-1] == {"type": "card", "card": None}
    hold.abandon()  # idempotent


def test_hold_rejects_a_non_positive_threshold():
    hold = HoldController(_controller()[0])
    with pytest.raises(ValueError):
        hold.set_handler(lambda: None, seconds=0)


def test_no_timer_anywhere_in_the_cards_module():
    # "Nothing times out or vanishes on its own." The only waits are
    # ask()'s optional, caller-supplied timeout. No threading.Timer, no
    # sleep, no asyncio.sleep.
    from pathlib import Path

    import saathi.screen.cards as cards_module

    source = Path(cards_module.__file__).read_text()
    assert "Timer(" not in source
    assert "sleep(" not in source
    assert "call_later" not in source
