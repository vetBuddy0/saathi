"""initiative/reminders.py: she sets a reminder by voice; at that time it's
said out loud, asked once more if she doesn't answer, and her family is
told only when she asked for that. A real IdentityStore on disk and a fake
clock; the server's half through its tick with a recording session."""

import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import saathi.screen.server as server_module
from saathi.core import Core, State
from saathi.identity.store import IdentityStore
from saathi.initiative.reminders import (
    ReminderWatch,
    Step,
    _due,
    family_line,
    make_reminder_tools,
    repeat_line,
    spoken_line,
)
from saathi.initiative.scheduler import scheduled_candidates
from saathi.screen.server import REMINDER_TICK, build_app
from tests.test_conversation_window import _window
from tests.test_screen_server import ChunkCapture, RecordingSession, _connect

SGT = timezone(timedelta(hours=8))
DAY = date(2026, 10, 20)  # a Tuesday


def at(hh: int, mm: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=SGT)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db() -> Path:
    path = Path(tempfile.mkdtemp()) / "identity.sqlite3"
    with IdentityStore(path) as store:
        store.create()
    return path


def _tools(db, clock):
    set_tool, list_tool, cancel_tool = make_reminder_tools(db, now=clock)
    return set_tool.handler, list_tool.handler, cancel_tool.handler


def _turn(db: Path, when: datetime) -> None:
    with IdentityStore(db) as store:
        store.append("turns", ts=when.astimezone(timezone.utc).isoformat(), mode="press")


def _reminders(db: Path, **filters) -> list[dict]:
    with IdentityStore(db) as store:
        return store.read("reminders", **filters)


def _initiatives(db: Path) -> list[dict]:
    with IdentityStore(db) as store:
        return store.read("initiatives", kind="scheduled")


# -- when ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "at_, day, minutes, expected",
    [
        ("20:00", None, None, at(20, 0)),  # later today
        ("08:00", None, None, at(8, 0, DAY + timedelta(days=1))),  # passed: tomorrow
        ("15:00", "tomorrow", None, at(15, 0, DAY + timedelta(days=1))),
        ("09:00", "friday", None, at(9, 0, date(2026, 10, 23))),
        ("09:00", "Tuesday", None, at(9, 0, date(2026, 10, 27))),  # today's 9:00 has passed
        ("18:00", "tuesday", None, at(18, 0)),  # today's 18:00 hasn't
        (None, None, 20, at(10, 20)),
    ],
)
def test_when_she_means(at_, day, minutes, expected):
    assert _due(at_, day, minutes, at(10, 0)) == expected


@pytest.mark.parametrize(
    "at_, day, minutes",
    [(None, None, None), ("8pm", None, None), ("08:00", "today", None),
     ("08:00", "someday", None), (None, None, 0)],
)
def test_what_it_refuses_with_a_reason(at_, day, minutes):
    with pytest.raises(ValueError):
        _due(at_, day, minutes, at(10, 0))


# -- the tools -------------------------------------------------------------------


def test_set_list_and_cancel(db):
    set_, list_, cancel = _tools(db, Clock(at(10, 0)))
    tablet = set_(text="take your blood pressure tablet", at="20:00", repeat="daily")
    assert tablet == {
        "status": "ok", "id": 1, "text": "take your blood pressure tablet", "at": "20:00",
        "day": "today", "repeat": "daily", "tell_family": False,
    }
    doctor = set_(text="leave for the doctor", at="15:00", day="tomorrow", tell_family=True)
    assert doctor["day"] == "tomorrow" and doctor["tell_family"] is True
    assert [r["id"] for r in list_()["reminders"]] == [1, 2]  # soonest first
    assert cancel(id=1)["status"] == "ok"
    assert [r["id"] for r in list_()["reminders"]] == [2]
    assert cancel(id=1)["status"] == "error"  # already gone
    assert cancel(id="the morning one")["status"] == "error"


def test_the_tool_says_why_it_refused(db):
    set_, _, _ = _tools(db, Clock(at(10, 0)))
    assert set_(text="", at="20:00")["status"] == "error"
    assert set_(text="x", at="20:00", repeat="monthly")["status"] == "error"
    assert "HH:MM" in set_(text="x", at="8pm")["detail"]
    assert _reminders(db) == []


# -- saying it -------------------------------------------------------------------


def test_due_is_said_in_her_words_once(db):
    clock = Clock(at(10, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00")
    watch = ReminderWatch(db, now=clock)
    clock.now = at(19, 59)
    assert watch.decide() == []
    clock.now = at(20, 0)
    [step] = watch.decide()
    assert step.action == "say"
    assert step.line == "It's 8 PM. Reminder: take your tablet."
    watch.spoken(step)
    assert _reminders(db, active=1) == []  # a one-off is done once said
    with IdentityStore(db) as store:
        assert scheduled_candidates(store, now=at(20, 1)) == []  # the scheduler agrees
    clock.now = at(20, 5)
    assert watch.decide() == []


def test_no_answer_is_asked_again_and_the_family_told_only_when_she_asked(db):
    clock = Clock(at(10, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00", tell_family=True)
    set_(text="water the plants", at="20:00")
    told = []
    watch = ReminderWatch(db, now=clock, tell_family=lambda t, body: told.append(body("Mum")) or 1)
    clock.now = at(20, 0)
    for step in watch.decide():
        watch.spoken(step)
    clock.now = at(20, 10)
    repeats = watch.decide()
    assert [s.line for s in repeats] == [repeat_line("take your tablet"),
                                         repeat_line("water the plants")]
    for step in repeats:
        watch.spoken(step)
    clock.now = at(20, 19)
    assert watch.decide() == []
    clock.now = at(20, 20)
    [tell] = watch.decide()  # only the one she asked about
    assert tell.action == "tell" and tell.text == "take your tablet"
    assert watch.tell(tell) == 1
    assert told == ['Mum didn\'t answer Melody\'s 8 PM reminder, asked twice: "take your tablet".']
    clock.now = at(21, 0)
    assert watch.decide() == []  # told once


def test_any_answer_settles_it(db):
    clock = Clock(at(10, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00", tell_family=True)
    watch = ReminderWatch(db, now=clock)
    clock.now = at(20, 0)
    [step] = watch.decide()
    watch.spoken(step)
    _turn(db, at(20, 1))  # "okay lah"
    clock.now = at(20, 30)
    assert watch.decide() == []


def test_a_daily_one_comes_back_tomorrow_still_telling_the_family(db):
    clock = Clock(at(10, 0))
    set_, list_, _ = _tools(db, clock)
    set_(text="take your tablet", at="08:00", day="tomorrow", repeat="daily", tell_family=True)
    watch = ReminderWatch(db, now=clock)
    clock.now = at(8, 0, DAY + timedelta(days=1))
    [step] = watch.decide()
    watch.spoken(step)
    [next_one] = list_()["reminders"]
    assert (next_one["at"], next_one["day"], next_one["repeat"]) == ("8:00", "tomorrow", "daily")
    with IdentityStore(db) as store:
        from saathi.identity.preferences import read_preference

        assert read_preference(store, f"tell_family:{next_one['id']}") == "1"


def test_a_device_off_for_days_says_a_daily_one_once_not_once_per_missed_day(db):
    clock = Clock(at(10, 0))
    set_, list_, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00", repeat="daily")
    clock.now = at(21, 0, DAY + timedelta(days=3))
    watch = ReminderWatch(db, now=clock)
    [step] = watch.decide()
    watch.spoken(step)
    assert watch.decide() == []
    [next_one] = list_()["reminders"]
    assert next_one["day"] == "tomorrow"


def test_held_is_logged_once_and_retried(db):
    clock = Clock(at(10, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00")
    watch = ReminderWatch(db, now=clock)
    clock.now = at(20, 0)
    for _ in range(3):
        [step] = watch.decide()
        watch.held(step, "a call is on screen")
    held = [r for r in _initiatives(db) if r["suppressed_by"] == "a call is on screen"]
    assert len(held) == 1
    assert watch.decide()[0].action == "say"  # still due


def test_without_the_family_app_the_miss_is_logged_once(db):
    clock = Clock(at(10, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your tablet", at="20:00", tell_family=True)
    watch = ReminderWatch(db, now=clock)
    for minute, expected in ((0, "say"), (10, "repeat")):
        clock.now = at(20, minute)
        [step] = watch.decide()
        assert step.action == expected
        watch.spoken(step)
    clock.now = at(20, 20)
    [tell] = watch.decide()
    assert watch.tell(tell) == 0
    assert any(r["suppressed_by"] == "the family app is off" for r in _initiatives(db))
    clock.now = at(20, 30)
    assert watch.decide() == []


def test_lines_never_add_anything():
    due = at(8, 30)
    assert spoken_line("take your tablet.", due) == "It's 8:30 AM. Reminder: take your tablet."
    assert spoken_line("eat lunch", at(12, 0)).startswith("It's 12 PM.")
    assert spoken_line("sleep", at(0, 15)).startswith("It's 12:15 AM.")
    assert repeat_line("take your tablet") == "Just checking: take your tablet."
    assert family_line(None, "take your tablet", due).startswith("She didn't answer")


def test_the_gate_is_policys_reminder_lane(db):
    watch = ReminderWatch(db)
    step = Step("say", 1, "x", at(8, 0))
    assert watch.may_speak(step, busy=False) is None
    assert watch.may_speak(step, busy=True) == "something else is already happening"


# -- the screen server's half ----------------------------------------------------


class FakeWatch:
    def __init__(self, steps):
        self.steps = steps
        self.log = []

    def decide(self):
        steps, self.steps = self.steps, []
        return steps

    def may_speak(self, step, *, busy):
        return "something else is already happening" if busy else None

    def spoken(self, step):
        self.log.append(f"spoken {step.reminder_id}")

    def held(self, step, why):
        self.log.append(f"held {step.reminder_id}: {why}")

    def tell(self, step):
        self.log.append(f"tell {step.reminder_id}")
        return 1


async def _next_state(ws) -> str:
    while True:
        message = await ws.receive_json()
        if message["type"] == "state":
            return message["state"]


async def test_one_is_said_per_tick_and_a_tell_still_goes(monkeypatch):
    monkeypatch.setattr(server_module, "Capture", ChunkCapture)
    ChunkCapture.instances.clear()
    session = RecordingSession()
    watch = FakeWatch([
        Step("say", 1, "take your tablet", at(8, 0), "It's 8:00. Reminder: take your tablet."),
        Step("say", 2, "water the plants", at(8, 0), "It's 8:00. Reminder: water the plants."),
        Step("tell", 3, "x", at(7, 0)),
    ])
    app = build_app(
        Core(initial=State.IDLE), session=session, capture_source_id="src",
        follow_up_seconds=0.5, listen_window=_window, reminders=watch,
    )
    async with TestClient(TestServer(app)) as client:
        async with client.ws_connect("/ws") as ws:
            await _connect(ws)
            await app[REMINDER_TICK]()
            assert [await _next_state(ws) for _ in range(3)] == [
                "attentive", "speaking", "attentive"]
    assert session.spoken == ["It's 8:00. Reminder: take your tablet."]
    assert watch.log == ["spoken 1", "tell 3"]  # 2 waits for the next tick, not "held"


async def test_mid_turn_it_is_held():
    session = RecordingSession()
    watch = FakeWatch([Step("say", 1, "x", at(8, 0), "line")])
    app = build_app(Core(initial=State.THINKING), session=session, reminders=watch)
    async with TestClient(TestServer(app)):
        await app[REMINDER_TICK]()
    assert session.spoken == []
    assert watch.log == ["held 1: something else is already happening"]


def test_today_for_the_family_says_answered_or_not_and_what_is_coming(db):
    from saathi.initiative.reminders import today_summary

    clock = Clock(at(7, 0))
    set_, _, _ = _tools(db, clock)
    set_(text="take your morning tablet", at="08:00")
    set_(text="take your evening tablet", at="20:00")
    set_(text="call the clinic", at="09:00")
    watch = ReminderWatch(db, now=clock)
    clock.now = at(8, 0)
    [step] = watch.decide()
    watch.spoken(step)
    _turn(db, at(8, 1))
    clock.now = at(9, 0)
    [step] = watch.decide()
    watch.spoken(step)
    summary = today_summary(db, at(9, 5))
    assert [(r["at"], r["state"]) for r in summary] == [
        ("8 AM", "answered"), ("9 AM", "just said"), ("8 PM", "coming up")]
