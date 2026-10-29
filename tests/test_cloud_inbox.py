"""cloud/inbox.py against a fake Firestore and a real IdentityStore:
messages come back oldest first in the sender's words and are said once;
her reply goes under the message; caregiver reminders become local ones
(an edit retires and replaces, inactive retires); reminder events go out
once each, in order."""

from datetime import datetime, timedelta, timezone

import pytest

from saathi.cloud.inbox import Inbox
from saathi.identity.store import IdentityStore
from saathi.initiative import reminders
from tests.fake_firestore import FakeFirestore

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)
DEV = "devices/dev1"


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "identity.sqlite3"
    with IdentityStore(p) as store:
        store.create()
    return p


def _inbox(client, path):
    return Inbox(client, "dev1", path, tz=timezone.utc)


def _message(client, msg_id, text, minutes, sender="Priya", **extra):
    client.put(f"{DEV}/inbox/{msg_id}", **{
        "from": sender, "fromUid": "uidP", "text": text,
        "createdAt": NOW + timedelta(minutes=minutes), **extra,
    })


def _reminder(client, rid, minutes, **fields):
    doc = {"text": "take your tablet", "time": "09:00", "repeat": "none", "active": True,
           "createdBy": "uidP", "updatedAt": NOW + timedelta(minutes=minutes)}
    doc.update(fields)
    client.put(f"{DEV}/reminders/{rid}", **doc)


def test_messages_come_oldest_first_in_their_own_words(path):
    client = FakeFirestore()
    _message(client, "m2", "Running late, home by 8.", 5)
    _message(client, "m1", "Good morning Ma!", 1)
    _message(client, "m0", "Already heard.", 0, spokenAt=NOW)
    _message(client, "m3", "   ", 6)
    lines = [m.line for m in _inbox(client, path).check(NOW)]
    assert lines == ["Message from Priya: Good morning Ma!", "Message from Priya: Running late, home by 8."]


def test_a_spoken_message_is_marked_and_logged_and_never_said_again(path):
    client = FakeFirestore()
    _message(client, "m1", "Good morning Ma!", 1)
    inbox = _inbox(client, path)
    (first,) = inbox.check(NOW)
    inbox.mark_spoken("m1", NOW + timedelta(minutes=2))
    assert client.docs[f"{DEV}/inbox/m1"]["spokenAt"] == NOW + timedelta(minutes=2)
    assert client.docs[f"{DEV}/events/message-m1"] == {
        "ts": NOW + timedelta(minutes=2), "kind": "message_spoken",
        "text": "Message from Priya: Good morning Ma!",
    }
    _message(client, "m2", "Lunch at 1?", 3)
    assert [m.id for m in inbox.check(NOW)] == ["m2"]
    # The next poll asks only for what came after the last one said.
    (_, _, _, where, order_by, _) = [c for c in client.calls if c[0] == "query" and c[2] == "inbox"][-1]
    assert where == [("createdAt", ">=", NOW + timedelta(minutes=1))] and order_by == "createdAt"


def test_a_failed_cloud_write_still_never_repeats_the_message(path):
    client = FakeFirestore()
    _message(client, "m1", "Good morning Ma!", 1)
    inbox = _inbox(client, path)
    inbox.check(NOW)
    client.offline = True
    with pytest.raises(Exception):
        inbox.mark_spoken("m1", NOW)
    client.offline = False
    assert "spokenAt" not in client.docs[f"{DEV}/inbox/m1"]
    assert inbox.check(NOW) == []


def test_her_reply_goes_under_the_message(path):
    client = FakeFirestore()
    inbox = _inbox(client, path)
    inbox.record_reply("m1", " Tell her I love her ", NOW)
    inbox.record_reply("m1", "  ", NOW)  # nothing said: nothing sent
    assert list(client.children(f"{DEV}/inbox/m1/replies").values()) == [
        {"ts": NOW, "text": "Tell her I love her"}
    ]


def test_a_caregiver_reminder_becomes_a_local_one(path):
    client = FakeFirestore()
    _reminder(client, "r1", 0, time="09:30", date="2026-10-30", repeat="daily")
    inbox = _inbox(client, path)
    assert inbox.sync_reminders(NOW) == ["r1"]
    assert inbox.sync_reminders(NOW) == []  # unchanged: nothing to do
    with IdentityStore(path) as store:
        (row,) = store.read("reminders", active=1)
    assert row["text"] == "take your tablet" and row["recurrence"] == "daily"
    assert reminders.parse_utc(row["due_at"]) == datetime(2026, 10, 30, 9, 30, tzinfo=timezone.utc)


def test_an_edit_replaces_it_and_inactive_retires_it(path):
    client = FakeFirestore()
    _reminder(client, "r1", 0)
    inbox = _inbox(client, path)
    inbox.sync_reminders(NOW)
    _reminder(client, "r1", 5, text="take two tablets", time="08:00", repeat="weekdays")
    assert inbox.sync_reminders(NOW) == ["r1"]
    with IdentityStore(path) as store:
        rows = store.read("reminders")
    assert [(r["text"], r["active"]) for r in rows] == [
        ("take your tablet", 0), ("take two tablets", 1),
    ]
    assert rows[1]["recurrence"] == "weekdays"
    _reminder(client, "r1", 9, active=False)
    inbox.sync_reminders(NOW)
    with IdentityStore(path) as store:
        assert store.read("reminders", active=1) == []
    # Only reminders changed since the last one seen are asked for.
    query = [c for c in client.calls if c[0] == "query" and c[2] == "reminders"][-1]
    assert query[3] == [("updatedAt", ">=", NOW + timedelta(minutes=5))]


def test_a_reminder_with_no_usable_time_is_skipped_not_retried(path):
    client = FakeFirestore()
    _reminder(client, "r1", 0, time="", date=None)
    _reminder(client, "r2", 1, date="2020-01-01")
    _reminder(client, "r3", 2, repeat="hourly")
    inbox = _inbox(client, path)
    inbox.sync_reminders(NOW)
    assert inbox.sync_reminders(NOW) == []
    with IdentityStore(path) as store:
        assert store.read("reminders") == []


def test_reminder_events_go_out_once_each_in_order(path):
    client = FakeFirestore()
    _reminder(client, "r1", 0, time="01:05", date="2026-10-29")
    inbox = _inbox(client, path)
    inbox.sync_reminders(NOW)
    with IdentityStore(path) as store:
        (row,) = store.read("reminders")
        mine = store.append("reminders", due_at=NOW.isoformat(), text="water the plants",
                            recurrence=None, active=1)
        for rid, kind, minutes in ((row["id"], "fired", 5), (mine, "fired", 6),
                                   (row["id"], "acked", 7), (mine, "missed", 40)):
            store.append("reminder_events", reminder_id=rid, due_at=NOW.isoformat(),
                         ts=(NOW + timedelta(minutes=minutes)).isoformat(), kind=kind)
    client.offline = True
    with pytest.raises(Exception):
        inbox.push_events(NOW)
    client.offline = False
    assert inbox.push_events(NOW) == 4
    assert inbox.push_events(NOW) == 0
    events = sorted(client.children(f"{DEV}/events").values(), key=lambda e: e["ts"])
    assert [(e["kind"], e["text"], e.get("reminderId")) for e in events] == [
        ("fired", "take your tablet", "r1"),
        ("fired", "water the plants", None),  # set by her voice: no caregiver id
        ("acked", "take your tablet", "r1"),
        ("missed", "water the plants", None),
    ]
    assert events[0]["ts"] == NOW + timedelta(minutes=5)


def test_a_failing_reminder_sync_does_not_hold_back_messages(path, monkeypatch):
    client = FakeFirestore()
    _message(client, "m1", "Good morning Ma!", 1)
    inbox = _inbox(client, path)

    def broken(_now):
        raise RuntimeError("bad reminder doc")

    monkeypatch.setattr(inbox, "sync_reminders", broken)
    assert [m.id for m in inbox.check(NOW)] == ["m1"]


def test_empty_or_already_spoken_messages_move_the_cursor_only_past_nothing_unsaid(path):
    client = FakeFirestore()
    _message(client, "m0", "", 0)
    _message(client, "m1", "Good morning Ma!", 1)
    _message(client, "m2", "", 2)  # after an unsaid one: must not hide it
    inbox = _inbox(client, path)
    assert [m.id for m in inbox.check(NOW)] == ["m1"]
    with IdentityStore(path) as store:
        links = store.read("cloud_links", kind="message")
    assert [(r["remote_id"], r["version"]) for r in links] == [("m0", NOW.isoformat())]
    assert [m.id for m in inbox.check(NOW)] == ["m1"]


def test_a_malformed_reminder_does_not_hold_back_the_others(path):
    client = FakeFirestore()
    _reminder(client, "r1", 0, repeat=1)
    _reminder(client, "r2", 1, text={"oops": True})
    _reminder(client, "r3", 2)
    inbox = _inbox(client, path)
    assert inbox.sync_reminders(NOW) == ["r1", "r2", "r3"]
    with IdentityStore(path) as store:
        assert [r["text"] for r in store.read("reminders", active=1)] == ["take your tablet"]
