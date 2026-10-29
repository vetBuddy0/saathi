"""tools/reminders.py: "remind me at 9 / in 20 minutes / every day",
resolved against an injected clock on her wall clock, and the voice
"done" that clears the reminder card."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from saathi.identity.store import IdentityStore
from saathi.initiative import reminders
from saathi.screen.cards import CardController, choice, confirm
from saathi.tools.reminders import make_reminder_tools, resolve

SG = ZoneInfo("Asia/Singapore")
# Thursday 29 October 2026, 21:30 in Singapore.
NOW = datetime(2026, 10, 29, 21, 30, tzinfo=SG).astimezone(timezone.utc)


def _sg(*args) -> datetime:
    return datetime(*args, tzinfo=SG).astimezone(timezone.utc)


@pytest.fixture
def store(tmp_path: Path):
    s = IdentityStore(tmp_path / "identity.sqlite3")
    s.create()
    yield s
    s.close()


def _tools(store, cards=None, now=NOW):
    return {tool.name: tool.handler for tool, _ in make_reminder_tools(store, cards=cards, clock=lambda: now, tz=SG)}


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"in_minutes": 20}, NOW + timedelta(minutes=20)),
        ({"time_text": "22:00"}, _sg(2026, 10, 29, 22, 0)),
        ({"time_text": "9"}, _sg(2026, 10, 30, 9, 0)),  # 9 has passed: tomorrow
        ({"time_text": "08:15", "day": "tomorrow"}, _sg(2026, 10, 30, 8, 15)),
        ({"time_text": "10:00", "day": "monday"}, _sg(2026, 11, 2, 10, 0)),
        ({"time_text": "20:00", "day": "thursday"}, _sg(2026, 11, 5, 20, 0)),  # today, passed
        ({"time_text": "10:00", "day": "2026-12-25"}, _sg(2026, 12, 25, 10, 0)),
    ],
)
def test_her_words_resolve_to_a_time_on_her_clock(kwargs, expected):
    assert resolve(NOW, tz=SG, **kwargs) == expected


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"time_text": "25:00"}, {"time_text": "soon"}, {"in_minutes": 0},
     {"time_text": "10:00", "day": "2026-01-01"}, {"time_text": "20:00", "day": "today"}],
)
def test_no_usable_time_is_refused_not_guessed(kwargs):
    assert resolve(NOW, tz=SG, **kwargs) is None


def test_set_list_and_cancel(store):
    tools = _tools(store)
    made = tools["set_reminder"](text="take your blood pressure tablet", time="09:00", repeat="daily")
    assert made["status"] == "ok" and made["first"] == "tomorrow at 09:00" and made["repeat"] == "daily"
    row = store.read("reminders", id=made["id"])[0]
    assert row["recurrence"] == "daily" and row["due_at"] == _sg(2026, 10, 30, 9, 0).isoformat()

    once = tools["set_reminder"](text="call Priya", in_minutes=30)
    assert once["first"] == "today at 22:00"
    listed = tools["list_reminders"]()["reminders"]
    assert [(r["text"], r["repeat"], r["next"]) for r in listed] == [
        ("take your blood pressure tablet", "daily", "tomorrow at 09:00"),
        ("call Priya", "none", "today at 22:00"),
    ]

    assert tools["cancel_reminder"](id=once["id"]) == {"status": "ok", "cancelled": "call Priya"}
    assert tools["cancel_reminder"](id=once["id"])["status"] == "not_found"
    assert [r["text"] for r in tools["list_reminders"]()["reminders"]] == ["take your blood pressure tablet"]


def test_a_reminder_with_no_time_asks_rather_than_guesses(store):
    tools = _tools(store)
    assert tools["set_reminder"](text="take your tablet")["status"] == "needs_time"
    assert tools["set_reminder"](text="  ", in_minutes=5)["status"] == "error"
    assert tools["set_reminder"](text="x", in_minutes=5, repeat="hourly")["status"] == "error"
    assert store.read("reminders") == []


def test_saying_done_marks_the_reminder_and_clears_its_card(store):
    cards = CardController()
    tools = _tools(store, cards=cards)
    assert tools["reminder_done"]()["status"] == "nothing_pending"

    rid = store.append("reminders", due_at=NOW.isoformat(), text="take your tablet", recurrence=None, active=1)
    (action,) = reminders.due(store, NOW)
    reminders.record(store, action, "fired", NOW)
    cards.show(confirm(reminders.card_title([action]), spoken="A reminder: take your tablet."))
    assert tools["reminder_done"]() == {"status": "ok", "done": ["take your tablet"]}
    assert cards.current is None
    assert store.read("reminders", id=rid)[0]["active"] == 0


def test_done_leaves_any_other_card_alone(store):
    cards = CardController()
    tools = _tools(store, cards=cards)
    store.append("reminders", due_at=NOW.isoformat(), text="take your tablet", recurrence=None, active=1)
    (action,) = reminders.due(store, NOW)
    reminders.record(store, action, "fired", NOW)
    song = choice("Which song?", ["One", "Two"])
    cards.show(song)
    tools["reminder_done"]()
    assert cards.current is song
