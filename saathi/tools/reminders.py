"""Reminders she sets, lists, cancels and marks done, by talking.

Why it exists: the owner asked (2026-10-29) for "if I tell it anything
it remembers" -- medicine first. `initiative/reminders.py` decides when
one is due; these tools are how one gets there and how she says it is
done.

The tool resolves the time, not the model: the model passes what she
said ("at 9", "in 20 minutes", "tomorrow", "every day") and the handler
reads the real clock. Lost: giving the model today's date and time in
its context and letting it write an ISO timestamp -- the context is
compiled between turns (CLAUDE.md), so the clock in it is as old as the
last turn, and an hour-stale "now" makes "in 20 minutes" wrong.

Each handler opens its own connection: tools run on the turn's executor
thread, never the one that opened the runtime's store (the same fix as
`tools/language.py`).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Callable

from saathi.identity.store import IdentityStore
from saathi.initiative import reminders
from saathi.tools.registry import Tool

PERMISSION = "reminders"

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

SET_REMINDER_DESCRIPTION = (
    "Set a reminder when she asks to be reminded of something (medicine, a call, "
    "an appointment), or agrees when you offer one. `text` is what to say to her "
    "when it is due, addressed to her (\"take your blood pressure tablet\"), in the "
    "language she is speaking. Give either `in_minutes` (\"in half an hour\" = 30) "
    "or `time` as 24-hour HH:MM on her clock (\"9 at night\" = \"21:00\"), with `day` "
    "if she named one. `repeat` only if she said it repeats (\"every day\" = daily). "
    "Then tell her, in your own words, the time the result says."
)
LIST_REMINDERS_DESCRIPTION = (
    "List her reminders, when she asks what reminders she has, or before cancelling one."
)
CANCEL_REMINDER_DESCRIPTION = (
    "Cancel one reminder by its id (from list_reminders), when she asks to stop or "
    "remove it."
)
REMINDER_DONE_DESCRIPTION = (
    "Mark the reminder you just gave her as done, when she says she has done it "
    "(\"I took it\", \"done\", \"yes\" right after a reminder). Not for anything else."
)


def _parse_time(text: str) -> time | None:
    match = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*", text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _first_day(day: str | None, today: date) -> date | None:
    """The date `day` names, from `today`: "today", "tomorrow", a
    weekday (the next one, today included), or YYYY-MM-DD."""
    if not day:
        return today
    word = day.strip().lower()
    if word == "today":
        return today
    if word == "tomorrow":
        return today + timedelta(days=1)
    if word in _DAYS:
        return today + timedelta(days=(_DAYS.index(word) - today.weekday()) % 7)
    try:
        return date.fromisoformat(word)
    except ValueError:
        return None


def resolve(
    now: datetime,
    *,
    time_text: str | None = None,
    in_minutes: int | None = None,
    day: str | None = None,
    tz: tzinfo | None = None,
) -> datetime | None:
    """The first due time, UTC, or None if there is no usable one. A
    clock time already past today means tomorrow when no day was named
    ("remind me at 8" said at 9pm), and next week when a weekday was
    named and it is that day; an explicit day in the past is refused."""
    if in_minutes is not None:
        minutes = int(in_minutes)
        return now + timedelta(minutes=minutes) if minutes > 0 else None
    clock = _parse_time(time_text or "")
    if clock is None:
        return None
    today = reminders.local(now, tz).date()
    first = _first_day(day, today)
    if first is None:
        return None
    moment = reminders.at(first, clock, tz)
    if moment > now:
        return moment
    word = (day or "").strip().lower()
    if not word:
        return reminders.at(today + timedelta(days=1), clock, tz)
    if word in _DAYS:
        return reminders.at(first + timedelta(days=7), clock, tz)
    return None


def describe(moment: datetime, now: datetime, tz: tzinfo | None = None) -> str:
    """"today at 21:00", "tomorrow at 09:00", "Monday 3 November at 09:00"."""
    when, today = reminders.local(moment, tz), reminders.local(now, tz).date()
    clock = when.strftime("%H:%M")
    if when.date() == today:
        return f"today at {clock}"
    if when.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"{when.strftime('%A')} {when.day} {when.strftime('%B')} at {clock}"


def make_reminder_tools(
    store: IdentityStore,
    *,
    cards=None,
    clock: Callable[[], datetime] | None = None,
    tz: tzinfo | None = None,
) -> list[tuple[Tool, str]]:
    path = store.path
    now_of = clock or (lambda: datetime.now(timezone.utc))

    def _set_reminder(
        text: str,
        time: str | None = None,
        in_minutes: int | None = None,
        day: str | None = None,
        repeat: str | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"status": "error", "detail": "a reminder needs something to say"}
        recurrence = (repeat or "none").strip().lower()
        if recurrence not in reminders.RECURRENCES:
            allowed = list(reminders.RECURRENCES)
            return {"status": "error", "detail": f"repeat must be one of {allowed}"}
        now = now_of()
        first = resolve(now, time_text=time, in_minutes=in_minutes, day=day, tz=tz)
        if first is None:
            return {
                "status": "needs_time",
                "detail": "No usable time: ask her when, then call again with time or in_minutes.",
            }
        with IdentityStore(path) as own:
            reminder_id = own.append(
                "reminders",
                due_at=first.isoformat(),
                text=text,
                recurrence=None if recurrence == "none" else recurrence,
                active=1,
            )
        return {
            "status": "ok",
            "id": reminder_id,
            "text": text,
            "first": describe(first, now, tz),
            "repeat": recurrence,
        }

    def _list_reminders() -> dict[str, Any]:
        now = now_of()
        with IdentityStore(path) as own:
            rows = own.read("reminders", active=1)
        listed = []
        for row in rows:
            upcoming = reminders.next_occurrence(row, now, tz)
            listed.append(
                {
                    "id": row["id"],
                    "text": row["text"],
                    "repeat": row["recurrence"] or "none",
                    "next": describe(upcoming, now, tz) if upcoming else "due now",
                }
            )
        return {"status": "ok", "reminders": listed}

    def _cancel_reminder(id: int) -> dict[str, Any]:
        with IdentityStore(path) as own:
            rows = own.read("reminders", id=int(id), active=1)
            if not rows:
                return {"status": "not_found", "detail": "no active reminder with that id"}
            own.retire("reminders", int(id), now_of())
        return {"status": "ok", "cancelled": rows[0]["text"]}

    def _reminder_done() -> dict[str, Any]:
        with IdentityStore(path) as own:
            done = reminders.acknowledge(own, now_of())
        if not done:
            return {"status": "nothing_pending", "detail": "no reminder was given lately"}
        current = cards.current if cards is not None else None
        if current is not None and current.title.startswith(reminders.CARD_PREFIX):
            cards.clear()
        return {"status": "ok", "done": [action.text for action in done]}

    integer = {"type": "integer"}
    text_field = {"type": "string"}
    return [
        (
            Tool(
                name="set_reminder",
                schema={
                    "type": "object",
                    "properties": {
                        "text": text_field,
                        "time": {"type": "string", "description": "HH:MM, 24-hour, her clock"},
                        "in_minutes": integer,
                        "day": {
                            "type": "string",
                            "description": "today, tomorrow, a weekday name, or YYYY-MM-DD",
                        },
                        "repeat": {"type": "string", "enum": list(reminders.RECURRENCES)},
                    },
                    "required": ["text"],
                },
                permission=PERMISSION,
                handler=_set_reminder,
            ),
            SET_REMINDER_DESCRIPTION,
        ),
        (
            Tool(
                name="list_reminders",
                schema={"type": "object", "properties": {}},
                permission=PERMISSION,
                handler=_list_reminders,
            ),
            LIST_REMINDERS_DESCRIPTION,
        ),
        (
            Tool(
                name="cancel_reminder",
                schema={"type": "object", "properties": {"id": integer}, "required": ["id"]},
                permission=PERMISSION,
                handler=_cancel_reminder,
            ),
            CANCEL_REMINDER_DESCRIPTION,
        ),
        (
            Tool(
                name="reminder_done",
                schema={"type": "object", "properties": {}},
                permission=PERMISSION,
                handler=_reminder_done,
            ),
            REMINDER_DONE_DESCRIPTION,
        ),
    ]
