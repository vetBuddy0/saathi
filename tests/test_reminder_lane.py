"""The reminder lane in screen/server.py: a due reminder is said between
turns only, with a "Done?" card whose Yes marks it done; why it fired
is in `initiatives`; and it waits for a turn, a call, or another card."""

import asyncio
from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.core import Core, Event, State
from saathi.initiative import reminders
from saathi.screen.call_panel import CallPanel, CallView
from saathi.screen.cards import CardController, choice
from saathi.screen.server import CHECK_REMINDERS, build_app
from tests.test_screen_server import FakeSession, _connect, _tmp_store

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)


class SpokeFirstSession(FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.said_first_lines: list[str] = []

    def said_first(self, text: str, why: str = "") -> None:
        self.said_first_lines.append(text)
        self.said_first_why = why


def _due(store, text="take your tablet") -> int:
    return store.append("reminders", due_at=NOW.isoformat(), text=text, recurrence=None, active=1)


def _app(store, core, session=None, **kwargs):
    return build_app(
        core,
        session=session or SpokeFirstSession(),
        capture_source_id="src",
        store=store,
        reminder_tick_seconds=None,
        # Her taps on a card happen at the test's time, not the real one:
        # "done" counts only within DONE_WITHIN of the reminder being said.
        reminder_now=lambda: NOW,
        **kwargs,
    )


async def _settle() -> None:
    await asyncio.sleep(0.1)


def test_saathi_may_speak_first_from_idle_or_asleep_only():
    for start in (State.IDLE, State.SLEEPING):
        core = Core(initial=start)
        assert core.handle(Event("initiative")) and core.state == State.SPEAKING
    for busy in (State.LISTENING, State.THINKING, State.SPEAKING, State.ATTENTIVE):
        assert not Core(initial=busy).handle(Event("initiative"))


async def test_a_due_reminder_is_said_with_a_done_card_and_a_reason_on_record():
    store = _tmp_store()
    rid = _due(store)
    cards = CardController()
    session = SpokeFirstSession()
    core = Core(initial=State.SLEEPING)
    app = _app(store, core, session, cards=cards)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            actions = app[CHECK_REMINDERS](NOW)
            assert [a.kind for a in actions] == ["fire"]
            await _settle()
            assert session.spoken == ["A reminder: take your tablet."]
            assert session.said_first_lines == ["A reminder: take your tablet."]
            assert "reminder_done" in session.said_first_why  # her "done" has a tool to reach
            assert cards.current.title == "Done? take your tablet"
            assert core.state == State.IDLE
            # Said once is enough until REPEAT_AFTER.
            assert app[CHECK_REMINDERS](NOW + timedelta(minutes=1)) == []
            # Her tap on Yes is "done".
            await ws.send_json({"type": "card_answer", "id": cards.current.id, "answer": {"yes": True}})
            await _settle()
    fired = store.read("initiatives", kind="scheduled")
    assert [row["suppressed_by"] for row in fired] == [None]
    assert f"[reminder_id={rid}]" in fired[0]["reason"]
    kinds = [event["kind"] for event in store.read("reminder_events", reminder_id=rid)]
    assert kinds == ["fired", "acked"]
    assert store.read("reminders", id=rid)[0]["active"] == 0
    store.close()


async def test_no_on_the_card_leaves_it_to_be_said_again():
    store = _tmp_store()
    _due(store)
    cards = CardController()
    session = SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            app[CHECK_REMINDERS](NOW)
            await _settle()
            await ws.send_json({"type": "card_answer", "id": cards.current.id, "answer": {"yes": False}})
            await _settle()
            later = NOW + reminders.REPEAT_AFTER
            app[CHECK_REMINDERS](later)
            await _settle()
    assert session.spoken == ["A reminder: take your tablet.", "Reminder again: take your tablet."]
    store.close()


async def test_a_reminder_waits_for_her_turn_and_for_a_call():
    store = _tmp_store()
    _due(store)
    calls = CallPanel()
    session = SpokeFirstSession()
    core = Core(initial=State.LISTENING)
    app = _app(store, core, session, calls=calls)
    async with TestClient(TestServer(app)):
        assert app[CHECK_REMINDERS](NOW) == []
        core.handle(Event("release"))
        core.handle(Event("no_response"))
        assert core.state == State.IDLE
        calls.update(CallView("Udhi", "+65", "ringing"))
        assert app[CHECK_REMINDERS](NOW) == []
        calls.update(None)
        assert [a.kind for a in app[CHECK_REMINDERS](NOW)] == ["fire"]
        await _settle()
    assert session.spoken == ["A reminder: take your tablet."]
    store.close()


async def test_a_card_left_up_holds_a_reminder_back_only_for_a_while(monkeypatch):
    store = _tmp_store()
    _due(store)
    cards = CardController()
    song = choice("Which song?", ["One", "Two"])
    cards.show(song)
    session = SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session, cards=cards)
    async with TestClient(TestServer(app)):
        assert app[CHECK_REMINDERS](NOW) == []
        monkeypatch.setattr(server_module, "_STALE_CARD_SECONDS", 0.0)
        assert [a.kind for a in app[CHECK_REMINDERS](NOW)] == ["fire"]
        await _settle()
    assert cards.current.title.startswith(reminders.CARD_PREFIX)
    assert session.spoken == ["A reminder: take your tablet."]
    store.close()


async def test_unanswered_reminders_end_missed():
    store = _tmp_store()
    rid = _due(store)
    session = SpokeFirstSession()
    app = _app(store, Core(initial=State.IDLE), session)
    async with TestClient(TestServer(app)):
        now = NOW
        for _ in range(reminders.ATTEMPTS):
            app[CHECK_REMINDERS](now)
            await _settle()
            now += reminders.REPEAT_AFTER
        assert [a.kind for a in app[CHECK_REMINDERS](now)] == ["miss"]
    assert len(session.spoken) == reminders.ATTEMPTS
    kinds = [event["kind"] for event in store.read("reminder_events", reminder_id=rid)]
    assert kinds == ["fired"] * reminders.ATTEMPTS + ["missed"]
    store.close()
