"""A reminder or a timer that comes due during a call, in screen/server.py:
shown on a card, silently, and said once the call is over.

Why it exists: the owner asked (2026-10-29) for the card to show up
"regardless ... even if in call", and chose a small silent card over
Saathi speaking (or chiming) over the call. What's pinned: nothing is
said and nothing is recorded while the call is on -- an attempt is a
saying -- the card is not put back up every tick, nor after she
dismissed it, but is after a Holding card pushed it off; it never
replaces another card (the "Answer?" of a ringing call); a Yes marks the
reminder done and an Okay stops the timer, and either way nothing is
said afterwards; otherwise the call's end brings the usual saying. The
layout is pinned in tests/test_call_card_layout.py.
"""

import asyncio
from datetime import timedelta

from aiohttp.test_utils import TestClient, TestServer

from saathi.core import Core, State
from saathi.initiative import reminders
from saathi.screen.call_panel import CallPanel, CallView
from saathi.screen.cards import CardController, confirm, holding
from saathi.screen.server import CHECK_CLOCK, CHECK_REMINDERS, build_app
from tests.test_clock_lane import _setup
from tests.test_reminder_lane import NOW, SpokeFirstSession, _app, _due
from tests.test_screen_server import _tmp_store


async def _settle() -> None:
    await asyncio.sleep(0.1)


def _events(store, rid) -> list[str]:
    return [event["kind"] for event in store.read("reminder_events", reminder_id=rid)]


def _on_the_phone(calls: CallPanel) -> None:
    calls.update(CallView("Priya", "+65", "connected"))


async def test_a_reminder_due_in_a_call_is_shown_silently_and_said_after():
    store = _tmp_store()
    rid = _due(store)
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        _on_the_phone(calls)
        assert app[CHECK_REMINDERS](NOW) == []
        await _settle()
        shown = cards.current
        assert shown.kind == "confirm" and shown.title == "Done? take your tablet"
        assert session.spoken == [] and session.said_first_lines == []
        # Not counted as an attempt, not logged as an utterance.
        assert _events(store, rid) == []
        assert store.read("initiatives") == []
        # The next tick leaves the same card up.
        assert app[CHECK_REMINDERS](NOW + timedelta(seconds=30)) == []
        assert cards.current.id == shown.id
        calls.update(None)
        assert [a.kind for a in app[CHECK_REMINDERS](NOW + timedelta(minutes=1))] == ["fire"]
        await _settle()
    assert session.spoken == ["A reminder: take your tablet."]
    assert cards.current.title == "Done? take your tablet" and cards.current.id != shown.id
    assert _events(store, rid) == ["fired"]
    store.close()


async def test_yes_on_the_silent_card_marks_it_done_and_nothing_is_said():
    store = _tmp_store()
    rid = _due(store)
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW)
        assert cards.answer(cards.current.id, {"yes": True}, source="tap")
        assert app[CHECK_REMINDERS](NOW) == []
        assert cards.current is None
        calls.update(None)
        assert app[CHECK_REMINDERS](NOW + timedelta(minutes=1)) == []
        await _settle()
    assert session.spoken == []
    assert _events(store, rid) == ["acked"]
    assert store.read("reminders", id=rid)[0]["active"] == 0
    store.close()


async def test_a_reminder_said_before_the_call_comes_again_silently_and_yes_ends_it():
    store = _tmp_store()
    rid = _due(store)
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        app[CHECK_REMINDERS](NOW)
        await _settle()
        assert cards.answer(cards.current.id, {"yes": False}, source="tap")
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW + reminders.REPEAT_AFTER)
        await _settle()
        assert session.spoken == ["A reminder: take your tablet."]
        assert cards.answer(cards.current.id, {"yes": True}, source="tap")
    assert _events(store, rid) == ["fired", "acked"]
    store.close()


async def test_dismissed_it_stays_down_for_the_call_pushed_off_it_comes_back():
    store = _tmp_store()
    rid = _due(store)
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW)
        # A spacebar hold to hang up replaces it; she lets go.
        hold_card = holding("Keep holding to hang up")
        cards.show(hold_card)
        cards.clear()
        app[CHECK_REMINDERS](NOW + timedelta(seconds=30))
        assert cards.current.title == "Done? take your tablet"
        # "Never mind": not again this call.
        assert cards.answer(cards.current.id, {"dismiss": True}, source="tap")
        app[CHECK_REMINDERS](NOW + timedelta(seconds=60))
        assert cards.current is None
        # A new call is a new chance to see it.
        calls.update(None)
        calls.update(CallView("Priya", "+65", "connected"))
        app[CHECK_REMINDERS](NOW + timedelta(seconds=90))
        assert cards.current.title == "Done? take your tablet"
        calls.update(None)
        app[CHECK_REMINDERS](NOW + timedelta(minutes=2))
        await _settle()
    assert session.spoken == ["A reminder: take your tablet."]
    assert _events(store, rid) == ["fired"]
    store.close()


async def test_a_second_reminder_in_the_call_does_not_bring_back_one_she_answered():
    store = _tmp_store()
    _due(store)
    later = store.append(
        "reminders",
        due_at=(NOW + timedelta(minutes=20)).isoformat(),
        text="drink water",
        recurrence=None,
        active=1,
    )
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW)
        assert cards.answer(cards.current.id, {"yes": False}, source="tap")
        app[CHECK_REMINDERS](NOW + timedelta(minutes=20))
        assert cards.current.title == "Done? drink water"
        assert cards.answer(cards.current.id, {"yes": True}, source="tap")
        calls.update(None)
        app[CHECK_REMINDERS](NOW + timedelta(minutes=21))
        await _settle()
    # Her "No" during the call leaves it to be said; her "Yes" does not.
    assert session.spoken == ["A reminder: take your tablet."]
    assert _events(store, later) == ["acked"]
    store.close()


async def test_a_session_less_screen_shows_no_reminder_in_a_call():
    store = _tmp_store()
    _due(store)
    cards, calls = CardController(), CallPanel()
    app = build_app(
        Core(initial=State.IDLE),
        session=None,
        capture_source_id="src",
        store=store,
        reminder_tick_seconds=None,
        cards=cards,
        calls=calls,
    )
    async with TestClient(TestServer(app)):
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW)
        assert cards.current is None
    store.close()


async def test_a_ringing_calls_answer_card_is_not_replaced():
    store = _tmp_store()
    _due(store)
    cards, calls, session = CardController(), CallPanel(), SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards, calls=calls)
    async with TestClient(TestServer(app)):
        calls.update(CallView("Priya", "+65", "ringing"))
        answer = confirm("Priya is calling. Answer?")
        cards.show(answer)
        app[CHECK_REMINDERS](NOW)
        assert cards.current.id == answer.id
    assert session.spoken == []
    store.close()


async def test_a_timer_done_in_a_call_is_shown_silently_and_said_after():
    core = Core(initial=State.IDLE)
    calls, cards = CallPanel(), CardController()
    app, clock, now, session, store = _setup(core, calls=calls, cards=cards)
    async with TestClient(TestServer(app)):
        clock.start_timer(60, "tea")
        _on_the_phone(calls)
        now.value += 60
        assert app[CHECK_CLOCK]() == []
        shown = cards.current
        assert (shown.kind, shown.title, shown.value) == ("readback", "Timer done", "Tea")
        assert app[CHECK_CLOCK]() == []
        assert cards.current.id == shown.id
        await _settle()
        assert session.spoken == [] and store.read("initiatives") == []
        calls.update(None)
        assert [t.label for t in app[CHECK_CLOCK]()] == ["tea"]
        await _settle()
    assert session.spoken == ["The tea timer is done."]
    # Said, so the card goes and the clock behind it shows "Time's up".
    assert cards.current is None
    assert len(clock.timers) == 1
    store.close()


async def test_okay_on_the_timer_card_stops_it_and_nothing_is_said():
    core = Core(initial=State.IDLE)
    calls, cards = CallPanel(), CardController()
    app, clock, now, session, store = _setup(core, calls=calls, cards=cards)
    async with TestClient(TestServer(app)):
        clock.start_timer(60)
        _on_the_phone(calls)
        now.value += 60
        app[CHECK_CLOCK]()
        assert cards.current.value == "1 minute"
        assert cards.answer(cards.current.id, {"dismiss": True}, source="tap")
        assert clock.timers == []
        app[CHECK_CLOCK]()
        assert cards.current is None
        calls.update(None)
        assert app[CHECK_CLOCK]() == []
        await _settle()
    assert session.spoken == []
    store.close()


async def test_a_timer_card_left_after_the_call_goes_once_too_late_to_say():
    core = Core(initial=State.LISTENING)  # busy when the call ends
    calls, cards = CallPanel(), CardController()
    app, clock, now, session, store = _setup(core, calls=calls, cards=cards)
    async with TestClient(TestServer(app)):
        clock.start_timer(60, "tea")
        _on_the_phone(calls)
        now.value += 60
        app[CHECK_CLOCK]()
        calls.update(None)
        app[CHECK_CLOCK]()
        assert cards.current.title == "Timer done"  # still to be said
        now.value += 301
        app[CHECK_CLOCK]()
        assert cards.current is None
    assert session.spoken == []
    store.close()


async def test_a_reminder_waits_behind_the_timer_card_not_the_other_way():
    calls, cards = CallPanel(), CardController()
    app, clock, now, session, store = _setup(Core(initial=State.IDLE), calls=calls, cards=cards)
    _due(store)
    async with TestClient(TestServer(app)):
        clock.start_timer(60, "eggs")
        _on_the_phone(calls)
        app[CHECK_REMINDERS](NOW)
        assert cards.current.title == "Done? take your tablet"
        now.value += 60
        app[CHECK_CLOCK]()
        assert cards.current.title == "Timer done"
        app[CHECK_REMINDERS](NOW + timedelta(seconds=30))
        assert cards.current.title == "Timer done"
    assert session.spoken == []
    store.close()
