"""initiative/reminders.py: when each occurrence is due on her wall
clock, and an occurrence's life -- said, said again, done or missed --
read back from `reminder_events`. Real store, injected clock and zone."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from saathi.identity.store import IdentityStore
from saathi.initiative import reminders

SG = ZoneInfo("Asia/Singapore")
LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def store(tmp_path: Path):
    s = IdentityStore(tmp_path / "identity.sqlite3")
    s.create()
    yield s
    s.close()


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _local(tz, *args) -> datetime:
    return datetime(*args, tzinfo=tz).astimezone(timezone.utc)


def _add(store, due_at: datetime, text="take your tablet", recurrence=None) -> int:
    return store.append("reminders", due_at=due_at.isoformat(), text=text, recurrence=recurrence, active=1)


def test_a_daily_reminder_keeps_its_wall_clock_time_across_a_clock_change():
    # 09:00 in London on the Saturday before the clocks go back (BST, UTC+1).
    first = _local(LONDON, 2026, 10, 24, 9, 0)
    row = {"due_at": first.isoformat(), "recurrence": "daily"}
    monday = _local(LONDON, 2026, 10, 26, 12, 0)  # GMT now, UTC+0
    assert reminders.occurrence(row, monday, LONDON) == _local(LONDON, 2026, 10, 26, 9, 0)
    assert reminders.occurrence(row, monday, LONDON).hour == 9  # 09:00 GMT is 09:00 UTC
    assert reminders.next_occurrence(row, monday, LONDON) == _local(LONDON, 2026, 10, 27, 9, 0)


def test_weekdays_skip_the_weekend_and_weekly_keeps_the_first_day():
    first = _local(SG, 2026, 10, 30, 8, 0)  # a Friday
    weekdays = {"due_at": first.isoformat(), "recurrence": "weekdays"}
    weekly = {"due_at": first.isoformat(), "recurrence": "weekly"}
    sunday = _local(SG, 2026, 11, 1, 10, 0)
    # On Sunday the latest weekday occurrence is still Friday's.
    assert reminders.occurrence(weekdays, sunday, SG) == first
    assert reminders.next_occurrence(weekdays, sunday, SG) == _local(SG, 2026, 11, 2, 8, 0)
    assert reminders.next_occurrence(weekly, sunday, SG) == _local(SG, 2026, 11, 6, 8, 0)


def test_nothing_is_due_before_the_first_time_and_a_one_off_has_no_next():
    first = _utc(2026, 10, 29, 12, 0)
    row = {"due_at": first.isoformat(), "recurrence": None}
    assert reminders.occurrence(row, first - timedelta(seconds=1), SG) is None
    assert reminders.next_occurrence(row, first - timedelta(seconds=1), SG) == first
    assert reminders.next_occurrence(row, first + timedelta(hours=1), SG) is None


def test_a_due_reminder_is_said_then_said_again_then_missed(store):
    first = _utc(2026, 10, 29, 1, 0)
    rid = _add(store, first, "take your blood pressure tablet")
    now = first
    seen = []
    for _ in range(reminders.ATTEMPTS):
        (action,) = reminders.due(store, now)
        assert action.kind == "fire" and action.reminder_id == rid
        seen.append(action.attempt)
        reminders.record(store, action, "fired", now)
        # Not again until REPEAT_AFTER has passed.
        assert reminders.due(store, now + reminders.REPEAT_AFTER - timedelta(seconds=1)) == []
        now += reminders.REPEAT_AFTER
    assert seen == [1, 2, 3]
    (action,) = reminders.due(store, now)
    assert action.kind == "miss"
    reminders.record(store, action, "missed", now)
    assert reminders.due(store, now + timedelta(hours=1)) == []
    # A one-off that was missed is retired: nothing left to remind.
    assert store.read("reminders", id=rid)[0]["active"] == 0


def test_done_stops_the_repeats_and_retires_a_one_off(store):
    first = _utc(2026, 10, 29, 1, 0)
    rid = _add(store, first)
    (action,) = reminders.due(store, first)
    reminders.record(store, action, "fired", first)
    done = reminders.acknowledge(store, first + timedelta(minutes=2))
    assert [a.reminder_id for a in done] == [rid]
    assert reminders.due(store, first + timedelta(hours=1)) == []
    assert store.read("reminders", id=rid)[0]["active"] == 0
    # Nothing left to mark done.
    assert reminders.acknowledge(store, first + timedelta(minutes=3)) == []


def test_a_daily_reminder_done_today_comes_back_tomorrow(store):
    first = _local(SG, 2026, 10, 29, 9, 0)
    rid = _add(store, first, recurrence="daily")
    (action,) = reminders.due(store, first, SG)
    reminders.record(store, action, "fired", first)
    reminders.acknowledge(store, first + timedelta(minutes=1))
    assert reminders.due(store, first + timedelta(hours=5), SG) == []
    assert store.read("reminders", id=rid)[0]["active"] == 1
    tomorrow = first + timedelta(days=1)
    (action,) = reminders.due(store, tomorrow, SG)
    assert action.due_at == tomorrow and action.attempt == 1


def test_done_only_reaches_a_reminder_said_lately(store):
    first = _utc(2026, 10, 29, 1, 0)
    _add(store, first)
    (action,) = reminders.due(store, first)
    reminders.record(store, action, "fired", first)
    late = first + reminders.DONE_WITHIN + timedelta(minutes=1)
    assert reminders.pending(store, late) == []
    assert reminders.acknowledge(store, late) == []


def test_what_she_hears_is_her_own_words():
    first = reminders.Action("fire", 1, "take your tablet.", _utc(2026, 1, 1), 1, True)
    again = reminders.Action("fire", 2, "drink water", _utc(2026, 1, 1), 2, True)
    assert reminders.line([first]) == "A reminder: take your tablet."
    assert reminders.line([first, again]) == "A reminder: take your tablet. Reminder again: drink water."
    assert reminders.card_title([first, again]) == "Done? take your tablet; drink water"
