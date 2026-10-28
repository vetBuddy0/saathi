"""screen/board.py and the server's side of the screen's tabs (2026-10-28).

What this pins: a family message or a reminder reaches the screen as a
`shown` card (her words, who sent it, when), not only as a caption; the
tabs read her messages, her reminders by day (repeats on every day
they'll fire), and who she can ring; and a tap can ask only for the
tools `SCREEN_TOOLS` names, through the same handler her voice uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.core import Core, State
from saathi.identity.store import IdentityStore
from saathi.initiative.reminders import Step
from saathi.screen.board import SCREEN_TOOLS, _is_loopback, family_list, reminder_days
from saathi.screen.server import MESSAGE_TICK, REMINDER_TICK, build_app
from tests.test_conversation_window import _window
from tests.test_family_hub import _hub, _identity, _pair
from tests.test_screen_server import ChunkCapture, RecordingSession, _connect

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=SGT)


def _reminders(path, *rows):
    with IdentityStore(path) as store:
        for due, text, recurrence in rows:
            store.append("reminders", due_at=due.isoformat(), text=text,
                         recurrence=recurrence, active=1)


async def _until(ws, kind):
    while True:
        message = await ws.receive_json()
        if message["type"] == kind:
            return message


# -- what the tabs read --------------------------------------------------------------


def test_reminders_by_day_repeat_where_they_will_fire_and_sort_by_time(tmp_path):
    path = _identity(tmp_path)
    _reminders(
        path,
        (NOW.replace(hour=20), "take your tablet", "daily"),
        (NOW.replace(hour=15), "call Priya", None),
        (NOW + timedelta(days=2, hours=1), "water the plants", "weekly"),
    )
    board = reminder_days(path, NOW, days=9)  # 28 Oct - 5 Nov
    days = board["by_day"]
    assert board["today"] == "2026-10-28"
    assert [r["text"] for r in days["2026-10-28"]] == ["call Priya", "take your tablet"]
    assert days["2026-10-28"][0]["at"] == "3 PM"
    assert sum("take your tablet" in [r["text"] for r in v] for v in days.values()) == 9
    plants = [d for d, v in days.items() if any(r["text"] == "water the plants" for r in v)]
    assert plants == ["2026-10-30"]  # weekly: the next is 6 Nov, past the window
    assert sum("call Priya" in [r["text"] for r in v] for v in days.values()) == 1


def test_a_retired_reminder_is_not_on_the_calendar(tmp_path):
    path = _identity(tmp_path)
    _reminders(path, (NOW + timedelta(hours=1), "gone", None))
    with IdentityStore(path) as store:
        [row] = store.read("reminders")
        store.retire("reminders", row["id"], NOW.isoformat())
    assert reminder_days(path, NOW)["by_day"] == {}


def test_messages_read_newest_first_with_who_and_whose(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Lunch on Sunday?")
    hub.from_her("Udhi", "Yes please")
    [mine, theirs] = hub.recent()
    assert mine["from_her"] is True and mine["who"] == "Udhi" and mine["text"] == "Yes please"
    assert theirs["from_her"] is False and theirs["text"] == "Lunch on Sunday?"


def test_a_card_is_who_and_their_words_untouched(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Lunch on Sunday?")
    message_id, _line = hub.next_for_her()
    card = hub.card(message_id)
    assert card["kind"] == "message" and card["from"] == "Udhi"
    assert card["text"] == "Lunch on Sunday?" and card["at"].startswith("Today")
    assert hub.card(999) is None


def test_family_list_is_everyone_paired(tmp_path):
    _hub_, registry = _hub(tmp_path)
    _pair(registry, "Priya", "daughter", "Mum", subscribed=False)
    assert family_list(registry) == [{"name": "Priya", "relation": "daughter", "alerts": False}]


def test_only_loopback_may_read_the_board():
    class Req:
        def __init__(self, remote):
            self.remote = remote

    assert _is_loopback(Req("127.0.0.1")) and _is_loopback(Req("::1"))
    assert not _is_loopback(Req("192.168.1.20")) and not _is_loopback(Req(None))


async def test_the_board_routes_answer_with_her_data_or_a_plain_reason(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Hello Amma")
    path = tmp_path / "identity.sqlite3"  # _hub made it
    app = build_app(Core(initial=State.IDLE), messages=hub, identity_db=path)
    async with TestClient(TestServer(app)) as client:
        messages = await (await client.get("/screen-local/board/messages")).json()
        reminders = await (await client.get("/screen-local/board/reminders")).json()
        family = await (await client.get("/screen-local/board/family")).json()
        missing = await client.get("/screen-local/board/nonsense")
    assert messages["ok"] and messages["items"][0]["text"] == "Hello Amma"
    assert reminders["ok"] and reminders["by_day"] == {}
    assert family == {"ok": False, "reason": family["reason"]} and "family app" in family["reason"]
    assert missing.status == 404


# -- what appears while she hears it -------------------------------------------------


async def test_a_message_being_read_to_her_shows_as_a_card(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Lunch on Sunday?")
    session = RecordingSession()
    app = build_app(Core(initial=State.IDLE), session=session, capture_source_id="src",
                    follow_up_seconds=0.5, listen_window=_window, messages=hub)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await app[MESSAGE_TICK]()
            card = await _until(ws, "shown")
    assert card == {"type": "shown", "kind": "message", "from": "Udhi",
                    "text": "Lunch on Sunday?", "at": card["at"]}
    assert session.spoken == ["A message from Udhi: Lunch on Sunday?"]


async def test_a_reminder_being_said_shows_as_a_card(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)

    class Watch:
        def decide(self):
            return [Step("say", 1, "take your tablet", NOW.replace(hour=9),
                         "It's 9:00. Reminder: take your tablet.")]

        def may_speak(self, step, *, busy):
            return None

        def spoken(self, step):
            pass

    app = build_app(Core(initial=State.IDLE), session=RecordingSession(),
                    capture_source_id="src", follow_up_seconds=0.5, listen_window=_window,
                    reminders=Watch())
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await app[REMINDER_TICK]()
            card = await _until(ws, "shown")
    assert card["kind"] == "reminder" and card["text"] == "take your tablet"
    assert card["at"].endswith(("AM", "PM"))


# -- what a tap may do ---------------------------------------------------------------


async def test_a_tap_asks_only_for_screen_tools_through_her_own_handler():
    asked = []

    def intent(name, args):
        asked.append((name, args))
        return {"status": "calling", "note": "for the model"}

    app = build_app(Core(initial=State.IDLE), screen_intent=intent)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "screen_action", "tool": "call_contact",
                                "args": {"contact": "Priya"}})
            ok = await _until(ws, "screen_action_result")
            await ws.send_json({"type": "screen_action", "tool": "cancel_reminder",
                                "args": {"id": 1}})
            refused = await _until(ws, "screen_action_result")
            await ws.send_json({"type": "screen_action", "tool": "call_contact", "args": "x"})
            malformed = await _until(ws, "screen_action_result")
    assert asked == [("call_contact", {"contact": "Priya"})]
    assert ok["result"]["status"] == "calling"
    assert refused["result"] == {"status": "refused"}
    assert malformed["result"] == {"status": "refused"}
    assert SCREEN_TOOLS == {"call_contact"}


async def test_with_no_handler_every_tap_is_refused():
    app = build_app(Core(initial=State.IDLE))
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await ws.send_json({"type": "screen_action", "tool": "call_contact",
                                "args": {"contact": "Priya"}})
            result = await _until(ws, "screen_action_result")
    assert result["result"] == {"status": "refused"}
