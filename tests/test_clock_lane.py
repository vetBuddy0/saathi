"""The clock in screen/server.py: a finished timer is said once she is
free, with why on record; it waits for her turn and for a call, but not
for a card; the face's close and Stop taps reach the clock; and a fresh
screen is told what the clock shows."""

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from saathi.core import Core, Event, State
from saathi.screen.call_panel import CallPanel, CallView
from saathi.screen.cards import CardController, choice
from saathi.screen.server import CHECK_CLOCK, build_app
from saathi.tools.clock import ClockController
from tests.test_reminder_lane import SpokeFirstSession
from tests.test_screen_server import _connect, _tmp_store


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _setup(core, **kwargs):
    import time

    now = FakeTime()
    now.value = time.time()
    clock = ClockController(clock=now)
    store = _tmp_store()
    session = SpokeFirstSession()
    app = build_app(
        core,
        session=session,
        capture_source_id="src",
        store=store,
        reminder_tick_seconds=None,
        clock=clock,
        clock_tick_seconds=None,
        **kwargs,
    )
    return app, clock, now, session, store


async def _settle() -> None:
    await asyncio.sleep(0.1)


async def test_a_finished_timer_is_said_once_with_why_on_record():
    core = Core(initial=State.SLEEPING)
    app, clock, now, session, store = _setup(core)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            assert (await ws.receive_json())["type"] == "clock"
            clock.start_timer(300, "tea")
            assert app[CHECK_CLOCK]() == []
            now.value += 300
            said = app[CHECK_CLOCK]()
            assert [t.label for t in said] == ["tea"]
            await _settle()
            assert session.spoken == ["The tea timer is done."]
            assert session.said_first_lines == ["The tea timer is done."]
            assert "timer" in session.said_first_why
            assert app[CHECK_CLOCK]() == []
            await _settle()
            assert session.spoken == ["The tea timer is done."]
    rows = store.read("initiatives", kind="scheduled")
    assert [row["reason"] for row in rows] == ["Timer done: The tea timer is done."]
    store.close()


async def test_a_timer_waits_for_her_turn_and_a_call_but_not_a_card():
    core = Core(initial=State.LISTENING)
    calls = CallPanel()
    cards = CardController()
    app, clock, now, session, store = _setup(core, calls=calls, cards=cards)
    async with TestClient(TestServer(app)):
        clock.start_timer(60)
        now.value += 60
        assert app[CHECK_CLOCK]() == []  # she is speaking
        core.handle(Event("release"))
        core.handle(Event("no_response"))
        calls.update(CallView("Priya", "+65", "connected"))
        assert app[CHECK_CLOCK]() == []  # on the phone
        calls.update(None)
        cards.show(choice("Which song?", ["One", "Two"]))
        assert [t.seconds for t in app[CHECK_CLOCK]()] == [60]
        await _settle()
    assert session.spoken == ["Your 1-minute timer is done."]
    store.close()


async def test_a_timer_not_said_within_five_minutes_is_left_to_the_screen():
    core = Core(initial=State.THINKING)
    app, clock, now, session, store = _setup(core)
    async with TestClient(TestServer(app)):
        clock.start_timer(60)
        now.value += 60
        assert app[CHECK_CLOCK]() == []
        now.value += 301
        core.handle(Event("no_response"))
        assert core.state == State.IDLE
        assert app[CHECK_CLOCK]() == []
    assert session.spoken == []
    store.close()


async def test_the_faces_close_and_stop_taps_reach_the_clock():
    core = Core(initial=State.IDLE)
    app, clock, now, session, store = _setup(core)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            first = await ws.receive_json()
            assert first["type"] == "clock" and first["visible"] is False
            timer = clock.start_timer(10)
            assert (await ws.receive_json())["visible"] is True
            now.value += 10
            app[CHECK_CLOCK]()
            await ws.send_json({"type": "clock_event", "action": "dismiss", "id": timer.id})
            await _settle()
            assert clock.timers == []
            await ws.send_json({"type": "clock_event", "action": "hide"})
            await _settle()
            assert clock.visible is False
            await ws.send_json({"type": "clock_event", "action": "dismiss", "id": 7})
            await _settle()
    store.close()
