"""The family inbox in screen/server.py, end to end: a real build_app over
aiohttp, a real IdentityStore and cloud/inbox.py against a fake
Firestore. A message is said once, between turns, in the sender's words,
with why on record; `spokenAt` is written; her next words in the window
that follows go back as the reply, and nothing after the conversation
closes does; a caregiver's reminder is said by the reminder lane, and
its "fired" goes back to the family."""

import asyncio
from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.cloud.inbox import Inbox
from saathi.core import Core, Event, State
from saathi.screen.call_panel import CallPanel, CallView
from saathi.screen.server import CHECK_INBOX, CHECK_REMINDERS, build_app
from tests.fake_firestore import FakeFirestore
from tests.test_conversation_window import QUIET, SPEECH, _feed, _next_state, _states, _window
from tests.test_screen_server import ChunkCapture, RecordingSession, _connect, _tmp_store

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)
DEV = "devices/dev1"
LINE = "Message from Priya: Good morning Ma! Call me after lunch."


class ReplySession(RecordingSession):
    """Says what it is given; her every turn is heard as `HER`."""

    HER = "Tell her I love her"

    def __init__(self) -> None:
        super().__init__()
        self.said_first_lines: list[str] = []
        self.said_first_why = ""
        self.last_heard = None

    def said_first(self, text: str, why: str = "") -> None:
        self.said_first_lines.append(text)
        self.said_first_why = why

    def end_turn(self) -> str:
        self.last_heard = self.HER
        return "I'll tell her."


def _message(client, msg_id="m1", text="Good morning Ma! Call me after lunch.", minutes=0):
    client.put(f"{DEV}/inbox/{msg_id}", **{
        "from": "Priya", "fromUid": "uidP", "text": text,
        "createdAt": NOW + timedelta(minutes=minutes),
    })


def _setup(monkeypatch, core=None, **kwargs):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    store = _tmp_store()
    client = FakeFirestore()
    inbox = Inbox(client, "dev1", store.path, tz=timezone.utc)
    session = ReplySession()
    app = build_app(
        core or Core(initial=State.IDLE),
        session=session,
        capture_source_id="src",
        store=store,
        reminder_tick_seconds=None,
        inbox=inbox,
        inbox_tick_seconds=None,
        listen_window=_window,
        follow_up_seconds=0.5,
        **kwargs,
    )
    return app, client, session, store


async def _settle() -> None:
    await asyncio.sleep(0.1)


async def test_a_message_is_said_once_marked_and_her_reply_goes_back(monkeypatch):
    app, client, session, store = _setup(monkeypatch)
    _message(client)
    # A caregiver's reminder, due five minutes from now.
    client.put(f"{DEV}/reminders/r1", text="take your tablet", time="01:05", date="2026-10-29",
               repeat="none", active=True, createdBy="uidP", updatedAt=NOW)
    async with TestClient(TestServer(app)) as http:
        async with http.ws_connect("/ws") as ws:
            await _connect(ws)
            said = await app[CHECK_INBOX](NOW)
            assert [m.id for m in said] == ["m1"]
            # Said in their words, then the eyes stay on her, listening.
            assert await _states(ws, 2) == ["speaking", "attentive"]
            assert session.spoken == [LINE]
            assert session.said_first_lines == [LINE]
            assert "reply" in session.said_first_why
            await _settle()
            assert "spokenAt" in client.docs[f"{DEV}/inbox/m1"]
            assert client.docs[f"{DEV}/events/message-m1"]["kind"] == "message_spoken"

            # She answers in the window, without her name.
            _feed(ChunkCapture.instances[-1], SPEECH, SPEECH, SPEECH, *[QUIET] * 6)
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            await _settle()
            replies = client.children(f"{DEV}/inbox/m1/replies")
            assert [r["text"] for r in replies.values()] == [ReplySession.HER]

            # Only that answer: the next turn in the same conversation is hers alone.
            _feed(ChunkCapture.instances[-1], SPEECH, SPEECH, SPEECH, *[QUIET] * 6)
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)
            assert await _next_state(ws) == "idle"
            await _settle()
            assert len(client.children(f"{DEV}/inbox/m1/replies")) == 1

            # Said once: the next poll finds nothing new to say.
            assert await app[CHECK_INBOX](NOW + timedelta(seconds=10)) == []
            assert session.spoken.count(LINE) == 1

            # The caregiver's reminder came down with that poll and is said
            # by the reminder lane when due; its "fired" goes back up.
            actions = app[CHECK_REMINDERS](NOW + timedelta(minutes=5))
            assert [a.kind for a in actions] == ["fire"]
            await _settle()
            assert session.spoken[-1] == "A reminder: take your tablet."
            assert await _states(ws, 2) == ["speaking", "attentive"]
            await app[CHECK_INBOX](NOW + timedelta(minutes=5, seconds=10))
    reasons = [row["reason"] for row in store.read("initiatives", kind="scheduled")]
    assert reasons[0] == "Message from Priya [message_id=m1]"
    events = [e for e in client.children(f"{DEV}/events").values() if e["kind"] == "fired"]
    assert [(e["text"], e["reminderId"]) for e in events] == [("take your tablet", "r1")]
    store.close()


async def test_a_message_waits_for_her_turn_and_for_a_call(monkeypatch):
    calls = CallPanel()
    core = Core(initial=State.LISTENING)
    app, client, session, store = _setup(monkeypatch, core=core, calls=calls)
    _message(client)
    async with TestClient(TestServer(app)):
        assert await app[CHECK_INBOX](NOW) == []  # she is speaking
        core.handle(Event("release"))
        core.handle(Event("no_response"))
        calls.update(CallView("Priya", "+65", "ringing"))
        assert await app[CHECK_INBOX](NOW) == []  # on the phone
        assert session.spoken == []
        assert "spokenAt" not in client.docs[f"{DEV}/inbox/m1"]
        calls.update(None)
        assert [m.id for m in await app[CHECK_INBOX](NOW)] == ["m1"]
        await _settle()
    assert session.spoken == [LINE]
    store.close()


async def test_words_after_the_conversation_closed_are_not_a_reply(monkeypatch):
    app, client, session, store = _setup(monkeypatch)
    _message(client, "m1", "First.", 0)
    _message(client, "m2", "Second.", 1)
    async with TestClient(TestServer(app)) as http:
        async with http.ws_connect("/ws") as ws:
            await _connect(ws)
            assert [m.id for m in await app[CHECK_INBOX](NOW)] == ["m1"]  # one per tick
            assert await _states(ws, 2) == ["speaking", "attentive"]
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)  # she says nothing
            assert await _next_state(ws) == "idle"
            # Later she talks to Saathi about something else.
            await ws.send_json({"type": "input", "event": "press"})
            await ws.send_json({"type": "input", "event": "release"})
            assert await _states(ws, 4) == ["listening", "thinking", "speaking", "attentive"]
            await _settle()
            assert client.children(f"{DEV}/inbox/m1/replies") == {}
            # The conversation over, the next message has its turn.
            _feed(ChunkCapture.instances[-1], *[QUIET] * 20)
            assert await _next_state(ws) == "idle"
            assert [m.id for m in await app[CHECK_INBOX](NOW)] == ["m2"]
            await _settle()
    assert session.spoken[0] == "Message from Priya: First."
    assert session.spoken[-1] == "Message from Priya: Second."
    store.close()


async def test_offline_says_nothing_and_changes_nothing(monkeypatch):
    app, client, session, store = _setup(monkeypatch)
    _message(client)
    client.offline = True
    async with TestClient(TestServer(app)):
        try:
            await app[CHECK_INBOX](NOW)
        except Exception as exc:
            assert "no network" in str(exc)
        else:
            raise AssertionError("an offline poll should fail loudly to the tick")
        client.offline = False
        assert [m.id for m in await app[CHECK_INBOX](NOW)] == ["m1"]
        await _settle()
    assert session.spoken == [LINE]
    store.close()


async def test_without_an_inbox_the_lane_is_off():
    app = build_app(Core(initial=State.IDLE), store=None)
    async with TestClient(TestServer(app)):
        assert await app[CHECK_INBOX]() == []
