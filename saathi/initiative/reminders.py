"""When each reminder is due, and whether she has done it yet.

Why it exists: the owner asked (2026-10-29) for reminders that work --
"if I tell it anything it remembers", medicine first. The pieces from
checkpoint 3 (`scheduler.py`, `policy.py`, `phrase.py`) fire a reminder
once and then treat it as resolved forever: there was no way to know
she had heard it, nothing repeated, and `recurrence` was never read.
SPEC.md's own rule is "due, and not yet acknowledged, fires".

So a reminder here is a rule, not a moment. Its row keeps the first
`due_at` and a `recurrence` (`daily`, `weekdays`, `weekly`, or none),
and each *occurrence* -- 9:00 today, 9:00 tomorrow -- is computed, never
written back (`IdentityStore` has no update, on purpose). What happened
to an occurrence lives in `reminder_events`: said (`fired`, once per
attempt), done (`acked`), or unanswered after every attempt (`missed`).

An occurrence is said, then said again after `REPEAT_AFTER` until she
says it is done, `ATTEMPTS` times in all (both adjustable in the
console's settings, `settings()`); after that it is `missed`
(Batch 3 tells the family). A one-off reminder that is done or missed is
retired, so it stops being listed. Lost: one attempt and done (a
medicine reminder she did not hear is the failure that matters) and
repeating until acknowledged with no limit (a device nagging an empty
room all afternoon).

Times are her wall clock. Each occurrence is the first one's local time
of day on a matching day, so "9 every morning" stays 9 across a clock
change. `tz=None` is the system's local zone -- right on the laptop and
on the tablet, whose libc follows Android's setting (the engine's logs
there print +0800) -- and tests pass a `ZoneInfo`. Lost: zoneinfo names
from the shell, which need the `tzdata` package on Android for the
same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from saathi.identity.store import IdentityStore

RECURRENCES = ("none", "daily", "weekdays", "weekly")

REPEAT_AFTER = timedelta(minutes=10)
ATTEMPTS = 3

# What the console offers for those two (identity/preferences.py's
# REMINDER_REPEAT_KEY and REMINDER_TRIES_KEY).
REPEAT_CHOICES = (5, 10, 15, 30)
TRIES_CHOICES = (1, 2, 3, 4, 5)

# How long after it was said a reminder can still be marked done by
# voice ("I took it"): long enough for her to go and do it.
DONE_WITHIN = timedelta(hours=3)

# The card shown with a reminder. Its title starts with this, so the
# voice path (`tools/reminders.py`) can clear it once she says done.
CARD_PREFIX = "Done? "


@dataclass(frozen=True)
class Action:
    kind: str  # "fire" | "miss"
    reminder_id: int
    text: str
    due_at: datetime  # this occurrence, UTC
    attempt: int  # which saying this is ("fire"); how many there were ("miss")
    one_off: bool


def parse_utc(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local(moment: datetime, tz: tzinfo | None = None) -> datetime:
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def at(day: date, clock: time, tz: tzinfo | None = None) -> datetime:
    """`clock` on `day` on her wall clock, as UTC."""
    naive = datetime.combine(day, clock.replace(tzinfo=None))
    aware = naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
    return aware.astimezone(timezone.utc)


def _recurrence(reminder: dict) -> str:
    value = (reminder.get("recurrence") or "none").strip().lower()
    return value if value in RECURRENCES else "none"


def _matches(recurrence: str, day: date, first_day: date) -> bool:
    if recurrence == "weekdays":
        return day.weekday() < 5
    if recurrence == "weekly":
        return day.weekday() == first_day.weekday()
    return True  # daily


def occurrence(reminder: dict, now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The latest occurrence at or before `now`, or None before the first."""
    first = parse_utc(reminder["due_at"])
    if first > now:
        return None
    recurrence = _recurrence(reminder)
    if recurrence == "none":
        return first
    first_local = local(first, tz)
    today = local(now, tz).date()
    for back in range(8):
        day = today - timedelta(days=back)
        if not _matches(recurrence, day, first_local.date()):
            continue
        moment = at(day, first_local.time(), tz)
        if first <= moment <= now:
            return moment
    return first


def next_occurrence(reminder: dict, now: datetime, tz: tzinfo | None = None) -> datetime | None:
    """The first occurrence after `now`; None for a one-off already past."""
    first = parse_utc(reminder["due_at"])
    if first > now:
        return first
    recurrence = _recurrence(reminder)
    if recurrence == "none":
        return None
    first_local = local(first, tz)
    today = local(now, tz).date()
    for ahead in range(9):
        day = today + timedelta(days=ahead)
        if not _matches(recurrence, day, first_local.date()):
            continue
        moment = at(day, first_local.time(), tz)
        if moment > now:
            return moment
    return None


def _events(store: IdentityStore, reminder_id: int, due_at: datetime) -> list[dict]:
    return store.read("reminder_events", reminder_id=reminder_id, due_at=due_at.isoformat())


def settings(store: IdentityStore) -> tuple[timedelta, int]:
    """The repeat gap and the number of tries, as set in the console,
    else the defaults. A stored value outside the choices is ignored."""
    from saathi.identity.preferences import (
        REMINDER_REPEAT_KEY,
        REMINDER_TRIES_KEY,
        read_preference,
    )

    def number(key: str, choices: tuple[int, ...], default: int) -> int:
        try:
            value = int(read_preference(store, key) or default)
        except ValueError:
            return default
        return value if value in choices else default

    minutes = number(REMINDER_REPEAT_KEY, REPEAT_CHOICES, int(REPEAT_AFTER.total_seconds() // 60))
    return timedelta(minutes=minutes), number(REMINDER_TRIES_KEY, TRIES_CHOICES, ATTEMPTS)


def due(
    store: IdentityStore,
    now: datetime,
    tz: tzinfo | None = None,
    *,
    repeat_after: timedelta = REPEAT_AFTER,
    attempts: int = ATTEMPTS,
) -> list[Action]:
    """What to do this tick: say a reminder (first time, or again), or
    give up on one. Nothing for an occurrence that is done, missed, or
    was said less than `repeat_after` ago."""
    actions = []
    for reminder in store.read("reminders", active=1):
        moment = occurrence(reminder, now, tz)
        if moment is None:
            continue
        events = _events(store, reminder["id"], moment)
        if any(event["kind"] in ("acked", "missed") for event in events):
            continue
        fired = [parse_utc(event["ts"]) for event in events if event["kind"] == "fired"]
        if fired and now - max(fired) < repeat_after:
            continue
        one_off = _recurrence(reminder) == "none"
        if len(fired) >= attempts:
            kind, attempt = "miss", len(fired)
        else:
            kind, attempt = "fire", len(fired) + 1
        actions.append(Action(kind, reminder["id"], reminder["text"], moment, attempt, one_off))
    return actions


def record(store: IdentityStore, action: Action, kind: str, now: datetime) -> None:
    """`fired`, `acked` or `missed` for this occurrence. A one-off that
    is done or missed is retired: there is nothing left to remind."""
    store.append(
        "reminder_events",
        reminder_id=action.reminder_id,
        due_at=action.due_at.isoformat(),
        ts=now.isoformat(),
        kind=kind,
    )
    if kind in ("acked", "missed") and action.one_off:
        store.retire("reminders", action.reminder_id, now)


def pending(store: IdentityStore, now: datetime) -> list[Action]:
    """Reminders said in the last `DONE_WITHIN` and not yet done or
    missed: what "I've done it" refers to. Newest first."""
    reminders = {row["id"]: row for row in store.read("reminders")}
    by_occurrence: dict[tuple[int, str], list[dict]] = {}
    for event in store.read("reminder_events"):
        by_occurrence.setdefault((event["reminder_id"], event["due_at"]), []).append(event)
    found = []
    for (reminder_id, due_at), events in by_occurrence.items():
        if any(event["kind"] in ("acked", "missed") for event in events):
            continue
        fired = [parse_utc(event["ts"]) for event in events if event["kind"] == "fired"]
        reminder = reminders.get(reminder_id)
        if not fired or reminder is None or now - max(fired) > DONE_WITHIN:
            continue
        found.append(
            (
                max(fired),
                Action(
                    "fire",
                    reminder_id,
                    reminder["text"],
                    parse_utc(due_at),
                    len(fired),
                    _recurrence(reminder) == "none",
                ),
            )
        )
    return [action for _, action in sorted(found, key=lambda pair: pair[0], reverse=True)]


def acknowledge(
    store: IdentityStore, now: datetime, reminder_id: int | None = None
) -> list[Action]:
    """Marks pending reminders done -- all of them, or the one asked
    for -- and returns what was marked."""
    done = [action for action in pending(store, now) if reminder_id in (None, action.reminder_id)]
    for action in done:
        record(store, action, "acked", now)
    return done


def line(actions: list[Action]) -> str:
    """What she hears: her own words, verbatim (SPEC: phrasing may not
    invent). The first time "A reminder: ...", after that "Reminder
    again: ..."; several due together in one breath."""
    parts = []
    for action in actions:
        text = action.text.strip().rstrip(".!")
        prefix = "A reminder" if action.attempt <= 1 else "Reminder again"
        parts.append(f"{prefix}: {text}.")
    return " ".join(parts)


def card_title(actions: list[Action]) -> str:
    return CARD_PREFIX + "; ".join(action.text.strip().rstrip(".!") for action in actions)
