"""Spoken reminders: she asks Melody to remind her, and at that time Melody
says it out loud, asks once more if she doesn't answer, and (only if she
asked for it) tells her family she missed it.

Why it exists: "remind me take my medicine at eight o'clock" was
transcribed perfectly on 2026-10-20 and nothing could act on it. The
`reminders` table, `scheduler.scheduled_candidates()` and policy.py's
reminder lane already existed, but no tool wrote a reminder and nothing
spoke one (initiative/__init__.py: "nothing calls `say()` from here").
The owner said "yes" to building it on 2026-10-20, with telling the
family off unless she asks for it per reminder.

Reuses, not parallels: due reminders come from `scheduled_candidates()`
(its dedup by `[reminder_id=N]` tag is what stops a second firing), and
the gate is `policy.reminder_may_fire()`: no quiet hours, held while
busy. Presence is passed as confirmed, because there is no presence
sensor; that's recorded in DECISIONS.md. A reminder to an empty room is
what the repeat and the family message are for.

What she hears is a template, never generated: "It's 8 PM. Reminder:
take your blood pressure tablet." The text is what the model wrote when
she set it, in her words turned to "your". SPEC.md: "phrasing may not
invent", and a dose reminder is the last place to risk it.

Answered means a `turns` row after it was spoken: she said something, so
she heard it. Lost: listening for "done" specifically. Any reply proves
she's there, and "okay lah" or "already took" shouldn't need the exact
word.

Every step is an `initiatives` row of kind "scheduled" tagged
`[reminder_id=N]`, so a restart resumes and the scheduler's dedup sees
it. A one-off reminder is retired once spoken; a daily or weekly one is
retired and the next occurrence appended (IdentityStore has no update,
the same constraint scheduler.py's docstring describes). Whether to tell
the family is a `preferences` row per reminder id (`tell_family:<id>`),
not a new column: the schema is one of the five interfaces.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from saathi.identity.preferences import read_preference, write_preference
from saathi.identity.store import IdentityStore
from saathi.initiative.policy import PolicyContext, reminder_may_fire
from saathi.initiative.scheduler import scheduled_candidates
from saathi.tools.registry import Tool

logger = logging.getLogger(__name__)

KIND = "scheduled"
REPEAT_AFTER_MINUTES = 10
TELL_AFTER_MINUTES = 10  # after the repeat
TICK_SECONDS = 30.0
RECURRENCES = ("daily", "weekly")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MAX_TEXT = 200

_TAG = re.compile(r"\[reminder_id=(\d+)\]")
_SPOKEN = "Reminder due:"  # scheduler.py's own wording for a fired reminder
_REPEATED = "Reminder repeated:"
_TOLD = "Reminder missed, family told:"


def _tell_key(reminder_id: int) -> str:
    return f"tell_family:{reminder_id}"


def _aware(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _clock(when: datetime) -> str:
    """"8:00", "21:30": read as a time by every voice tried."""
    return f"{when.hour}:{when.minute:02d}"


def _said(when: datetime) -> str:
    """"8 PM", "8:30 AM": how a person says it. A voice reads "20:00" as
    "twenty hundred"."""
    hour = when.hour % 12 or 12
    suffix = "AM" if when.hour < 12 else "PM"
    return f"{hour} {suffix}" if when.minute == 0 else f"{hour}:{when.minute:02d} {suffix}"


def _local_now() -> datetime:
    return datetime.now().astimezone()


def spoken_line(text: str, due: datetime) -> str:
    return f"It's {_said(due)}. Reminder: {text.strip().rstrip('.')}."


def repeat_line(text: str) -> str:
    return f"Just checking: {text.strip().rstrip('.')}."


def family_line(calls_her: str | None, text: str, due: datetime) -> str:
    who = (calls_her or "").strip() or "She"
    return (
        f"{who} didn't answer Melody's {_said(due)} reminder, asked twice: "
        f"\"{text.strip().rstrip('.')}\"."
    )


@dataclass(frozen=True)
class Step:
    """"say", "repeat" or "tell", for one reminder."""

    action: str
    reminder_id: int
    text: str
    due: datetime
    line: str = ""


TellFamily = Callable[[str, Callable[[str | None], str]], int]


class ReminderWatch:
    """`decide()` reads; `spoken()`, `held()` and `tell()` write. Each
    opens its own connection: they run on executor threads."""

    def __init__(
        self,
        store_path: Path,
        *,
        tell_family: TellFamily | None = None,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._path = Path(store_path)
        self._tell_family = tell_family
        self._now = now

    # -- reading --------------------------------------------------------------

    def _rows(self, store: IdentityStore, now: datetime) -> dict[int, list[dict]]:
        """Fired rows of the last day, by reminder: older ones are long
        answered or told, and reading them every tick would grow forever."""
        since = now - timedelta(days=1)
        by_id: dict[int, list[dict]] = {}
        for row in store.read("initiatives", kind=KIND):
            match = _TAG.search(row["reason"] or "")
            if match and row["suppressed_by"] is None and _aware(row["ts"]) >= since:
                by_id.setdefault(int(match.group(1)), []).append(row)
        return by_id

    def decide(self) -> list[Step]:
        """At most one step per reminder, oldest due first: say what's due,
        repeat what went unanswered, tell the family when she asked."""
        now = self._now()
        steps: list[Step] = []
        with IdentityStore(self._path) as store:
            for candidate in scheduled_candidates(store, now=now):
                reminder_id = int(_TAG.search(candidate.reason).group(1))
                [reminder] = store.read("reminders", id=reminder_id)
                due = _aware(reminder["due_at"]).astimezone(now.tzinfo)
                steps.append(
                    Step("say", reminder_id, reminder["text"], due,
                         spoken_line(reminder["text"], due))
                )
            turns = [_aware(row["ts"]) for row in store.read("turns")]
            for reminder_id, rows in self._rows(store, now).items():
                step = self._follow_up(store, reminder_id, rows, turns, now)
                if step is not None:
                    steps.append(step)
        return sorted(steps, key=lambda s: s.due)

    def _follow_up(self, store, reminder_id, rows, turns, now) -> Step | None:
        said = [_aware(r["ts"]) for r in rows if r["reason"].startswith(_SPOKEN)]
        if not said:
            return None
        first = min(said)
        if any(t >= first for t in turns):
            return None  # she answered
        repeated = [_aware(r["ts"]) for r in rows if r["reason"].startswith(_REPEATED)]
        told = any(r["reason"].startswith(_TOLD) for r in rows)
        [reminder] = store.read("reminders", id=reminder_id)
        text = reminder["text"]
        due = _aware(reminder["due_at"]).astimezone(now.tzinfo)
        if not repeated:
            if now - first >= timedelta(minutes=REPEAT_AFTER_MINUTES):
                return Step("repeat", reminder_id, text, due, repeat_line(text))
            return None
        if told or read_preference(store, _tell_key(reminder_id)) != "1":
            return None
        if now - min(repeated) >= timedelta(minutes=TELL_AFTER_MINUTES):
            return Step("tell", reminder_id, text, due)
        return None

    # -- writing --------------------------------------------------------------

    def _log(self, store: IdentityStore, prefix: str, step: Step, *, spoken: bool,
             suppressed_by: str | None = None) -> None:
        store.append(
            "initiatives",
            ts=self._now().astimezone(timezone.utc).isoformat(),
            kind=KIND,
            reason=f"{prefix} {step.text} [reminder_id={step.reminder_id}]",
            source_episode=None,
            spoken=1 if spoken else 0,
            suppressed_by=suppressed_by,
        )

    def may_speak(self, step: Step, *, busy: bool) -> str | None:
        """policy.py's reminder gate. None, or why it waits."""
        from saathi.initiative.scheduler import InitiativeCandidate

        decision = reminder_may_fire(
            InitiativeCandidate(kind=KIND, reason=step.text),
            PolicyContext(presence=True, busy=busy),
        )
        return None if decision.speak else decision.reason

    def spoken(self, step: Step) -> None:
        with IdentityStore(self._path) as store:
            if step.action == "repeat":
                self._log(store, _REPEATED, step, spoken=True)
                logger.info("reminder %d repeated", step.reminder_id)
                return
            self._log(store, _SPOKEN, step, spoken=True)
            self._roll_forward(store, step)
        logger.info("reminder %d spoken", step.reminder_id)

    def held(self, step: Step, why: str) -> None:
        """Not spoken this tick (a call, mid-turn): logged once per reason,
        then retried. The row is suppressed, so it isn't "fired"."""
        reason_prefix = "Reminder held:" if step.action == "say" else "Reminder repeat held:"
        with IdentityStore(self._path) as store:
            wanted = f"{reason_prefix} {step.text} [reminder_id={step.reminder_id}]"
            if any(
                r["reason"] == wanted and r["suppressed_by"] == why
                for r in store.read("initiatives", kind=KIND)
            ):
                return
            self._log(store, reason_prefix, step, spoken=False, suppressed_by=why)

    def tell(self, step: Step) -> int:
        reached = 0
        why_not = None
        if self._tell_family is None:
            why_not = "the family app is off"
        else:
            try:
                reached = self._tell_family(
                    "Melody", lambda who: family_line(who, step.text, step.due)
                )
            except Exception as exc:
                logger.warning("reminder %d: couldn't tell the family: %s", step.reminder_id, exc)
                why_not = f"sending failed: {type(exc).__name__}"
            if why_not is None and reached == 0:
                why_not = "no family phone is paired for notifications"
        with IdentityStore(self._path) as store:
            # Logged as done even when nobody could be told: retrying a
            # missed-dose message every 30 s would be worse than once.
            self._log(store, _TOLD, step, spoken=False)
            if why_not is not None:
                self._log(store, "Reminder family message not sent:", step, spoken=False,
                          suppressed_by=why_not)
        return reached

    def _roll_forward(self, store: IdentityStore, step: Step) -> None:
        [reminder] = store.read("reminders", id=step.reminder_id)
        store.retire("reminders", step.reminder_id, self._now())
        recurrence = reminder["recurrence"]
        if recurrence not in RECURRENCES:
            return
        every = timedelta(days=1 if recurrence == "daily" else 7)
        due = _aware(reminder["due_at"])
        now = self._now()
        while due <= now:
            due += every
        new_id = store.append(
            "reminders", due_at=due.isoformat(), text=reminder["text"],
            recurrence=recurrence, active=1,
        )
        if read_preference(store, _tell_key(step.reminder_id)) == "1":
            write_preference(store, _tell_key(new_id), "1")


# -- the tools ------------------------------------------------------------------

SET_REMINDER_DESCRIPTION = (
    "Set a reminder Melody will say out loud at that time. Use when she asks to be "
    "reminded of something. text: what to say, turned to 'your', short, e.g. "
    "'take your blood pressure tablet'. at: 24-hour HH:MM. day: 'today', "
    "'tomorrow' or a weekday name; leave it out for the next time that clock time "
    "comes. in_minutes instead of at for 'in 20 minutes'. repeat: 'daily' or "
    "'weekly' only if she says every day/week. tell_family: true only if she asks "
    "for her family to be told if she misses it. After it's set, say back the "
    "time and what it's for."
)
LIST_REMINDERS_DESCRIPTION = (
    "Her upcoming reminders, soonest first. Use when she asks what reminders she "
    "has, or before cancelling one."
)
CANCEL_REMINDER_DESCRIPTION = (
    "Cancel one reminder by its id from list_reminders. Use when she asks to stop or "
    "remove a reminder; list first if you don't have the id."
)


def _due(at: str | None, day: str | None, in_minutes: Any, now: datetime) -> datetime:
    """Raises ValueError with a sentence the model can repeat."""
    if in_minutes not in (None, ""):
        minutes = int(in_minutes)
        if not 1 <= minutes <= 24 * 60:
            raise ValueError("in_minutes must be between 1 and 1440")
        return (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    if not at:
        raise ValueError("give a time (at, HH:MM) or in_minutes")
    try:
        clock = time.fromisoformat(at.strip())
    except ValueError:
        raise ValueError(f"at={at!r} is not a 24-hour HH:MM time") from None
    clock = clock.replace(second=0, microsecond=0)
    today = now.date()
    word = (day or "").strip().lower()
    if word in ("", "next"):
        due = datetime.combine(today, clock, tzinfo=now.tzinfo)
        return due if due > now else due + timedelta(days=1)
    if word == "today":
        target = today
    elif word == "tomorrow":
        target = today + timedelta(days=1)
    elif word in WEEKDAYS:
        target = today + timedelta(days=(WEEKDAYS.index(word) - today.weekday()) % 7)
        if datetime.combine(target, clock, tzinfo=now.tzinfo) <= now:
            target += timedelta(days=7)  # "Friday" said on a Friday evening
    else:
        try:
            target = date.fromisoformat(word)
        except ValueError:
            raise ValueError(f"day={day!r}: use today, tomorrow or a weekday") from None
    due = datetime.combine(target, clock, tzinfo=now.tzinfo)
    if due <= now:
        raise ValueError(f"{target.isoformat()} {at} has already passed")
    return due


def _describe(row: dict, now: datetime) -> dict[str, Any]:
    due = _aware(row["due_at"]).astimezone(now.tzinfo)
    if due.date() == now.date():
        day = "today"
    elif due.date() == now.date() + timedelta(days=1):
        day = "tomorrow"
    else:
        day = WEEKDAYS[due.weekday()]
    return {
        "id": row["id"],
        "text": row["text"],
        "at": _clock(due),
        "day": day,
        "repeat": row["recurrence"] or "once",
    }


def make_reminder_tools(
    store_path: Path, *, now: Callable[[], datetime] = _local_now
) -> tuple[Tool, Tool, Tool]:
    path = Path(store_path)

    def _set(text: str, at: str | None = None, day: str | None = None,
             in_minutes: Any = None, repeat: str | None = None,
             tell_family: bool = False) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"status": "error", "detail": "text is empty"}
        if len(text) > MAX_TEXT:
            return {"status": "error", "detail": f"text is over {MAX_TEXT} characters"}
        repeat = (repeat or "").strip().lower() or None
        if repeat in ("none", "once"):
            repeat = None
        if repeat is not None and repeat not in RECURRENCES:
            return {"status": "error", "detail": "repeat must be daily, weekly or left out"}
        current = now()
        try:
            due = _due(at, day, in_minutes, current)
        except (TypeError, ValueError) as exc:
            return {"status": "error", "detail": str(exc)}
        with IdentityStore(path) as store:
            reminder_id = store.append(
                "reminders", due_at=due.isoformat(), text=text, recurrence=repeat, active=1
            )
            if tell_family is True:
                write_preference(store, _tell_key(reminder_id), "1")
            [row] = store.read("reminders", id=reminder_id)
        return {"status": "ok", **_describe(row, current), "tell_family": tell_family is True}

    def _list() -> dict[str, Any]:
        current = now()
        with IdentityStore(path) as store:
            rows = store.read("reminders", active=1)
        rows.sort(key=lambda r: _aware(r["due_at"]))
        return {"status": "ok", "reminders": [_describe(r, current) for r in rows]}

    def _cancel(id: Any) -> dict[str, Any]:  # noqa: A002 -- the model's word
        try:
            reminder_id = int(id)
        except (TypeError, ValueError):
            return {"status": "error", "detail": "id must be a number from list_reminders"}
        with IdentityStore(path) as store:
            rows = store.read("reminders", id=reminder_id)
            if not rows or not rows[0]["active"]:
                return {"status": "error", "detail": f"no active reminder {reminder_id}"}
            store.retire("reminders", reminder_id, now())
        return {"status": "ok", "cancelled": _describe(rows[0], now())}

    set_tool = Tool(
        name="set_reminder",
        schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "at": {"type": "string", "description": "24-hour HH:MM"},
                "day": {"type": "string"},
                "in_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                "repeat": {"type": "string", "enum": ["once", "daily", "weekly"]},
                "tell_family": {"type": "boolean"},
            },
            "required": ["text"],
        },
        permission="reminders",
        handler=_set,
    )
    list_tool = Tool(
        name="list_reminders",
        schema={"type": "object", "properties": {}},
        permission="reminders",
        handler=_list,
    )
    cancel_tool = Tool(
        name="cancel_reminder",
        schema={
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
        permission="reminders",
        handler=_cancel,
    )
    return set_tool, list_tool, cancel_tool


# -- for the family app -----------------------------------------------------------


def today_summary(store_path: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    """Today's reminders as the family sees them: what, when, and whether
    she answered. Never what she said in answer -- that it happened is
    all they get (the same line quiet_day.py draws)."""
    now = now or _local_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    out: list[dict[str, Any]] = []
    with IdentityStore(Path(store_path)) as store:
        turns = [_aware(row["ts"]) for row in store.read("turns")]
        rows_by_id: dict[int, list[dict]] = {}
        for row in store.read("initiatives", kind=KIND):
            match = _TAG.search(row["reason"] or "")
            if match and row["suppressed_by"] is None and _aware(row["ts"]) >= start:
                rows_by_id.setdefault(int(match.group(1)), []).append(row)
        for reminder_id, rows in rows_by_id.items():
            said = [_aware(r["ts"]) for r in rows if r["reason"].startswith(_SPOKEN)]
            if not said:
                continue
            [reminder] = store.read("reminders", id=reminder_id)
            first = min(said)
            if any(t >= first for t in turns):
                state = "answered"
            elif any(r["reason"].startswith(_TOLD) for r in rows):
                state = "no answer, family told"
            elif any(r["reason"].startswith(_REPEATED) for r in rows):
                state = "no answer yet, asked twice"
            else:
                state = "just said"
            due = _aware(reminder["due_at"]).astimezone(now.tzinfo)
            out.append({"at": _said(due), "text": reminder["text"], "state": state, "_t": due})
        for row in store.read("reminders", active=1):
            due = _aware(row["due_at"]).astimezone(now.tzinfo)
            if start <= due < end:
                out.append({"at": _said(due), "text": row["text"], "state": "coming up", "_t": due})
    out.sort(key=lambda item: item["_t"])
    for item in out:
        del item["_t"]
    return out
