"""call/family_hub.py and what it adds to the family app: messages both
ways, call history, how she is today, her reminders, and re-pairing on a
new address. Real SQLite files in tmp_path; the socket through aiohttp's
TestClient; pushes recorded, never sent."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aiohttp.test_utils import TestClient, TestServer

from saathi.call.family import FamilyRegistry, PairingError
from saathi.call.family_hub import (
    FamilyHub,
    call_outcome,
    make_message_tools,
    when_label,
)
from saathi.call.family_runtime import ADDRESS_FILE, FamilyRuntime
from saathi.call.family_server import build_family_app
from saathi.call.hangup import FakeHoldSeam
from saathi.call.push import Subscription
from saathi.call.webrtc import FamilyCalls
from saathi.identity.store import IdentityStore
from tests.test_family_server import SUB_JSON, FakePush


class RecordingPush(FakePush):
    def __init__(self):
        self.sent = []

    def send(self, subscription, payload, **kwargs):
        self.sent.append((subscription.endpoint, payload))


def _identity(tmp_path):
    path = tmp_path / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    return path


def _pair(registry, name="Udhi", relation="son", calls_her="Amma", subscribed=True):
    token, _ = registry.issue_pairing_token()
    member, key = registry.redeem(token, name, relation, calls_her)
    if subscribed:
        registry.set_subscription(member.id, Subscription.from_json(SUB_JSON))
        member = registry.get(member.id)
    return member, key


def _hub(tmp_path, push=None):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    hub = FamilyHub(registry, tmp_path / "family.sqlite3", _identity(tmp_path), push=push,
                    public_url=lambda: "https://melody.test")
    return hub, registry


# -- messages to her -------------------------------------------------------------


def test_a_message_waits_for_her_then_is_read_word_for_word(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    live = []
    hub.attach(udhi.id, live.append)
    reply = hub.send_to_her(udhi, "  Amma, I'll come   on Sunday for lunch. ")
    assert reply["message"]["text"] == "Amma, I'll come on Sunday for lunch."
    assert reply["message"]["read"] is False
    message_id, line = hub.next_for_her()
    assert line == "A message from Udhi: Amma, I'll come on Sunday for lunch."
    hub.mark_read(message_id)
    assert hub.next_for_her() is None
    assert live[-1]["message"]["read"] is True  # the sender sees "read to her"


def test_an_empty_message_is_refused(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    assert hub.send_to_her(udhi, "   ")["type"] == "error"
    assert hub.next_for_her() is None


def test_messages_are_read_oldest_first(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    priya, _ = _pair(registry, "Priya", "daughter", "Mum")
    hub.send_to_her(udhi, "first")
    hub.send_to_her(priya, "second")
    first_id, first = hub.next_for_her()
    assert first.endswith("first")
    hub.mark_read(first_id)
    assert hub.next_for_her()[1] == "A message from Priya: second"


# -- messages from her -------------------------------------------------------------


def test_her_words_reach_the_right_phone_by_name_or_relation(tmp_path):
    push = RecordingPush()
    hub, registry = _hub(tmp_path, push)
    udhi, _ = _pair(registry)
    priya, _ = _pair(registry, "Priya", "daughter", "Mum", subscribed=False)
    send, _ = make_message_tools(lambda: hub)
    assert send.handler(to="my son", text="Okay, I'll cook.") == {
        "status": "ok", "sent_to": ["Udhi"]}
    [(_, payload)] = push.sent
    assert payload["kind"] == "notice" and payload["title"] == "Message from Amma"
    assert payload["body"] == "Okay, I'll cook."
    assert payload["url"] == "https://melody.test/family/"
    assert hub.thread(udhi.id)[-1].direction == "from_her"
    assert send.handler(to="Priya", text="Thank you")["sent_to"] == ["Priya"]  # no push: no alerts
    assert len(push.sent) == 1
    assert send.handler(to="everyone", text="Good night")["sent_to"] == ["Udhi", "Priya"]


def test_an_unknown_person_is_named_back_with_who_is_paired(tmp_path):
    hub, registry = _hub(tmp_path)
    _pair(registry)
    send, _ = make_message_tools(lambda: hub)
    result = send.handler(to="Ravi", text="hello")
    assert result["status"] == "error" and "Paired: Udhi" in result["detail"]


def test_without_the_family_app_the_tools_say_so(tmp_path):
    send, read = make_message_tools(lambda: None)
    assert send.handler(to="Udhi", text="hi")["status"] == "unavailable"
    assert read.handler()["status"] == "unavailable"


def test_any_messages_reads_the_unread_and_marks_them(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Call me when you're free")
    _, read = make_message_tools(lambda: hub)
    result = read.handler()
    assert [(m["from"], m["text"]) for m in result["messages"]] == [
        ("Udhi", "Call me when you're free")]
    assert read.handler()["messages"] == []
    assert hub.next_for_her() is None  # not read twice


# -- calls ---------------------------------------------------------------------------


def test_call_history_is_per_member_in_words(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    priya, _ = _pair(registry, "Priya", "daughter", "Mum")
    hub.record_call(udhi, "out", "hung up", 192.4)
    hub.record_call(udhi, "out", "no answer", None)
    hub.record_call(priya, "in", "declined", None)
    calls = hub.calls(udhi.id)
    assert [(c["who"], c["outcome"]) for c in calls] == [
        ("She called", "Missed"), ("She called", "Talked 3:12")]
    assert [c["outcome"] for c in hub.calls(priya.id)] == ["She declined"]


def test_call_outcomes():
    assert call_outcome("in", "no answer", None) == "She didn't answer"
    assert call_outcome("in", "busy", None) == "She was busy"
    assert call_outcome("out", "declined", None) == "You declined"
    assert call_outcome("in", "hung up", 5.0) == "Talked 0:05"


def test_a_finished_call_reaches_the_history(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    udhi, _ = _pair(registry)
    calls = FamilyCalls(registry, push=RecordingPush(), public_url=lambda: None,
                        hold=FakeHoldSeam())
    calls._ensure_ticker = lambda: None
    ended = []
    calls.on_ended = lambda *args: ended.append(args)
    assert calls.dial(udhi)["status"] == "calling"
    calls.hangup()
    [(member, direction, reason, seconds)] = ended
    assert (member.id, direction, reason, seconds) == (udhi.id, "out", "hung up", None)


# -- how she is ----------------------------------------------------------------------


def test_status_says_when_she_last_talked_and_nothing_of_what(tmp_path):
    hub, _ = _hub(tmp_path)
    assert hub.status()["last_talked"] is None
    with IdentityStore(tmp_path / "identity.sqlite3") as store:
        store.append("turns", ts=datetime.now(timezone.utc).isoformat(), mode="press")
        store.append("episodes", ts=datetime.now(timezone.utc).isoformat(),
                     text="She said her knee hurts.", importance=3.0)
    status = hub.status()
    assert status["last_talked"].startswith("Today ")
    assert "knee" not in json.dumps(status)


def test_when_labels():
    now = datetime(2026, 10, 20, 15, 0).astimezone()
    assert when_label(now.replace(hour=9, minute=42).timestamp(), now) == "Today 9:42 AM"
    assert when_label((now - timedelta(days=1)).replace(hour=20, minute=0).timestamp(), now) == (
        "Yesterday 8 PM")
    assert when_label((now - timedelta(days=30)).timestamp(), now) == "20 Sep"


# -- reminders from the family ----------------------------------------------------------


def test_the_family_can_add_list_and_cancel_her_reminders(tmp_path):
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    added = hub.handle(udhi, {"type": "add_reminder", "text": "take your tablet",
                              "at": "20:00", "repeat": "daily", "tell_family": True})
    [reminder] = added["reminders"]
    assert (reminder["text"], reminder["at"], reminder["repeat"]) == (
        "take your tablet", "20:00", "daily")
    bad = hub.handle(udhi, {"type": "add_reminder", "text": "x", "at": "8pm"})
    assert "HH:MM" in bad["error"] and len(bad["reminders"]) == 1
    cancelled = hub.handle(udhi, {"type": "cancel_reminder", "id": reminder["id"]})
    assert cancelled["reminders"] == []


# -- the socket ------------------------------------------------------------------------


def _app(tmp_path, push=None):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    calls = FamilyCalls(registry, push=push, public_url=lambda: "https://s.test",
                        hold=FakeHoldSeam())
    calls._ensure_ticker = lambda: None
    hub = FamilyHub(registry, tmp_path / "family.sqlite3", _identity(tmp_path))
    app = build_family_app(registry, calls, push or FakePush(), hub=hub,
                           run_blocking=lambda fn, *a: fn(*a))
    return app, registry, hub


async def test_a_paired_phone_gets_extras_over_its_socket(tmp_path):
    app, registry, hub = _app(tmp_path)
    member, key = _pair(registry)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/family/ws") as ws:
            await ws.send_str(json.dumps({"type": "hello", "member": member.id, "key": key}))
            welcome = json.loads((await ws.receive()).data)
            assert welcome["extras"] is True
            await ws.send_str(json.dumps({"type": "send_message", "text": "Hello Amma"}))
            reply = json.loads((await ws.receive()).data)
            assert reply["type"] == "message" and reply["message"]["text"] == "Hello Amma"
            await ws.send_str(json.dumps({"type": "messages"}))
            while True:
                got = json.loads((await ws.receive()).data)
                if got["type"] == "messages":
                    break
            assert [m["text"] for m in got["messages"]] == ["Hello Amma"]
            await ws.send_str(json.dumps({"type": "status"}))
            while True:
                got = json.loads((await ws.receive()).data)
                if got["type"] == "status":
                    break
            assert got["last_talked"] is None
    assert hub.next_for_her()[1] == "A message from Udhi: Hello Amma"


async def test_a_call_token_page_gets_no_extras(tmp_path):
    app, registry, hub = _app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/family/ws") as ws:
            await ws.send_str(json.dumps({"type": "hello", "call_id": "nope", "call_token": "x"}))
            got = json.loads((await ws.receive()).data)
            assert got["type"] == "unauthorized"


# -- a new address ----------------------------------------------------------------------


def test_a_relink_token_gives_the_same_member_a_new_key_once(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    member, old_key = _pair(registry)
    token = registry.issue_relink(member.id)
    again, new_key = registry.redeem_relink(token)
    assert again.id == member.id and new_key != old_key
    assert registry.authenticate(member.id, new_key) is not None
    assert registry.authenticate(member.id, old_key) is None
    try:
        registry.redeem_relink(token)
        raise AssertionError("a relink link worked twice")
    except PairingError as exc:
        assert "already been used" in str(exc)


def test_a_relink_for_an_unpaired_phone_is_refused(tmp_path):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    member, _ = _pair(registry)
    token = registry.issue_relink(member.id)
    registry.revoke(member.id)
    try:
        registry.redeem_relink(token)
        raise AssertionError("an unpaired phone was relinked")
    except PairingError as exc:
        assert "unpaired" in str(exc)


async def test_relink_endpoint(tmp_path):
    app, registry, _ = _app(tmp_path)
    member, _ = _pair(registry)
    token = registry.issue_relink(member.id)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/family/api/relink", json={"token": token})
        data = await response.json()
        assert data["ok"] and data["member_id"] == member.id and data["calls_her"] == "Amma"
        again = await client.post("/family/api/relink", json={"token": token})
        assert again.status == 400


def _runtime(tmp_path, url, push):
    registry = FamilyRegistry(tmp_path / "family.sqlite3")
    runtime = FamilyRuntime(registry=registry, calls=None, server=None, route=None,
                            stable_url=None, tunnel=None)
    runtime.public_url = lambda: url
    runtime.push = push
    runtime.data_dir = tmp_path
    return runtime, registry


def test_only_a_changed_address_sends_each_phone_a_one_tap_relink(tmp_path):
    push = RecordingPush()
    runtime, registry = _runtime(tmp_path, "https://one.trycloudflare.com", push)
    member, _ = _pair(registry)
    _pair(registry, "Priya", "daughter", "Mum", subscribed=False)
    runtime._announce_new_address()
    assert push.sent == []  # first address ever: nothing to fix
    runtime._announce_new_address()
    assert push.sent == []  # same address
    runtime.public_url = lambda: "https://two.trycloudflare.com"
    runtime._announce_new_address()
    [(_, payload)] = push.sent  # Priya has no alerts, so no push
    assert payload["url"].startswith("https://two.trycloudflare.com/family/#relink=")
    token = payload["url"].split("#relink=")[1]
    assert registry.redeem_relink(token)[0].id == member.id
    assert (tmp_path / ADDRESS_FILE).read_text() == "https://two.trycloudflare.com"


# -- the device reads it to her ----------------------------------------------------------


async def test_the_device_reads_a_waiting_message_when_she_is_free(tmp_path, monkeypatch):
    import saathi.screen.server as server_module
    from saathi.core import Core, State
    from saathi.screen.server import MESSAGE_TICK, build_app
    from tests.test_conversation_window import _window
    from tests.test_screen_server import ChunkCapture, RecordingSession

    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    hub, registry = _hub(tmp_path)
    udhi, _ = _pair(registry)
    hub.send_to_her(udhi, "Lunch on Sunday?")
    busy = RecordingSession()
    app = build_app(Core(initial=State.THINKING), session=busy, messages=hub)
    async with TestClient(TestServer(app)):
        await app[MESSAGE_TICK]()
    assert busy.spoken == [] and hub.next_for_her() is not None  # waits, not lost
    free = RecordingSession()
    app = build_app(Core(initial=State.IDLE), session=free, capture_source_id="src",
                    follow_up_seconds=0.5, listen_window=_window, messages=hub)
    async with TestClient(TestServer(app)):
        await app[MESSAGE_TICK]()
    assert free.spoken == ["A message from Udhi: Lunch on Sunday?"]
    assert hub.next_for_her() is None
