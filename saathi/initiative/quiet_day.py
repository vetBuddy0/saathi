"""A quiet day: she hasn't said a word to Melody by late morning, so Melody
asks how she is, and if that gets no answer either, her family is told.

Why it exists: she lives alone, and the question her family carries every
day is "is she alright today?". Nothing answered it. The owner asked for
this on 2026-10-20 ("add."), after it was proposed as: ask her first,
then tell the family, never tell them what she said.

What it says, and what it may not say. The greeting is a fixed sentence
(`GREETING`), not generated: SPEC.md's "phrasing may not invent" holds
trivially, and there is no model call on a timer. The family is told
only that there has been no word from her and when Melody asked, never
anything she said on other days. Lost: telling them "she's fine today"
too (a daily all-clear). It would make Melody a monitor she reports to,
not her companion, and a message every day stops being read.

The signal is a `turns` row: one is written for every real exchange
(spacebar or her name), so "a turn today" is "she talked to Melody
today". Lost: counting a family-app call or a song she put on as signs
of life. Both start from a turn anyway on most days, and reading the
family database here would couple two stores for the rare day they
don't.

Not under `policy.py`'s social budget. This is a safety check, closer to
the reminder lane: it doesn't need confirmed presence (there is no
presence sensor, and her absence is the very thing being checked), and
it ignores the daily cap and cooldown. It has its own once-a-day limit:
one greeting, one message, both logged in `initiatives` as
kind="event" with a reason starting `REASON_PREFIX`, so a restart
mid-morning picks up where it was and "why did my phone say that" is
answered by the table.

Away: she can say she's going out or away for some days (`going_away`
tool), and the check is held until the date she gave. A day she talks
to Melody needs no "away" at all: any turn that day settles it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from saathi.identity.preferences import read_preference, write_preference
from saathi.identity.store import IdentityStore
from saathi.tools.registry import Tool

logger = logging.getLogger(__name__)

KIND = "event"
REASON_PREFIX = "quiet day:"
AWAY_KEY = "away_until"  # preferences: an ISO date, the last day she's away
GREETING = "Good morning! How are you today?"
DEFAULT_CHECK_AT = time(11, 0)
DEFAULT_WAIT_MINUTES = 30
# How often the screen server asks. A minute late is nothing here.
TICK_SECONDS = 60.0
MAX_AWAY_DAYS = 30

_GREETED = f"{REASON_PREFIX} asked how she is"
_NOTIFIED = f"{REASON_PREFIX} told the family"
_AWAY = f"{REASON_PREFIX} she said she is away"
_HELD = f"{REASON_PREFIX} greeting held"


@dataclass(frozen=True)
class QuietDayConfig:
    check_at: time = DEFAULT_CHECK_AT
    wait_minutes: int = DEFAULT_WAIT_MINUTES

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "QuietDayConfig | None":
        """`SAATHI_QUIET_DAY`: unset or "on" is 11:00, "off" turns it
        off, "HH:MM" moves it. `SAATHI_QUIET_DAY_WAIT_MINUTES`: how long
        after the greeting the family is told. A malformed value raises
        ValueError (said at startup, not guessed)."""
        raw = env.get("SAATHI_QUIET_DAY", "").strip().lower() or "on"
        if raw == "off":
            return None
        check_at = DEFAULT_CHECK_AT
        if raw != "on":
            try:
                check_at = time.fromisoformat(raw)
            except ValueError:
                raise ValueError(
                    f"SAATHI_QUIET_DAY={raw!r}; expected on, off or a time like 11:00"
                ) from None
        wait = env.get("SAATHI_QUIET_DAY_WAIT_MINUTES", "").strip()
        try:
            wait_minutes = int(wait) if wait else DEFAULT_WAIT_MINUTES
        except ValueError:
            raise ValueError(f"SAATHI_QUIET_DAY_WAIT_MINUTES={wait!r} is not a number") from None
        if wait_minutes < 1:
            raise ValueError("SAATHI_QUIET_DAY_WAIT_MINUTES must be at least 1")
        return cls(check_at=check_at, wait_minutes=wait_minutes)


@dataclass(frozen=True)
class Step:
    """What this tick should do: "greet", "notify" or "away"."""

    action: str
    detail: str = ""


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _aware(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


# (title, body for a member's `calls_her`) -> how many family phones it reached.
TellFamily = Callable[[str, Callable[[Any], str]], int]


class QuietDayWatch:
    """`decide()` reads; `greeted()`, `held()`, `away()` and `notify()`
    write the `initiatives` row that says what happened. Each call opens
    its own connection to `store_path`: they run on the server's executor
    threads, and an sqlite connection can't cross threads (the same rule
    as tools/language.py)."""

    def __init__(
        self,
        store_path: Path,
        config: QuietDayConfig,
        *,
        tell_family: TellFamily | None = None,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._path = Path(store_path)
        self.config = config
        self._tell_family = tell_family
        self._now = now

    @property
    def greeting(self) -> str:
        return GREETING

    # -- reading --------------------------------------------------------------

    def _today(self, now: datetime) -> tuple[datetime, list[dict]]:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with IdentityStore(self._path) as store:
            rows = [
                row
                for row in store.read("initiatives", kind=KIND)
                if (row["reason"] or "").startswith(REASON_PREFIX) and _aware(row["ts"]) >= start
            ]
        return start, rows

    def _spoke_since(self, since: datetime) -> bool:
        with IdentityStore(self._path) as store:
            # Newest last; reading all is fine for one person's turns,
            # and IdentityStore has no ORDER/LIMIT to ask for less.
            turns = store.read("turns")
        return any(_aware(row["ts"]) >= since for row in reversed(turns))

    def _away_until(self) -> date | None:
        with IdentityStore(self._path) as store:
            value = read_preference(store, AWAY_KEY)
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def decide(self) -> Step | None:
        now = self._now()
        if now.time() < self.config.check_at:
            return None
        start, rows = self._today(now)
        reasons = [row["reason"] for row in rows]
        if any(r.startswith((_NOTIFIED, _AWAY)) for r in reasons):
            return None  # done for today
        if self._spoke_since(start):
            return None
        away_until = self._away_until()
        if away_until is not None and away_until >= now.date():
            return Step("away", f"until {away_until.isoformat()}")
        greeted = [_aware(row["ts"]) for row in rows if row["reason"].startswith(_GREETED)]
        if not greeted:
            return Step("greet")
        asked_at = greeted[0]
        if now - asked_at >= timedelta(minutes=self.config.wait_minutes):
            return Step("notify", asked_at.astimezone(now.tzinfo).strftime("%H:%M"))
        return None

    # -- writing --------------------------------------------------------------

    def _log(self, reason: str, *, spoken: bool, suppressed_by: str | None) -> None:
        with IdentityStore(self._path) as store:
            store.append(
                "initiatives",
                ts=self._now().astimezone(timezone.utc).isoformat(),
                kind=KIND,
                reason=reason,
                source_episode=None,
                spoken=1 if spoken else 0,
                suppressed_by=suppressed_by,
            )

    def greeted(self) -> None:
        last = "no word from her yet today"
        self._log(f"{_GREETED} ({last})", spoken=True, suppressed_by=None)
        logger.info("quiet day: no word from her yet today; asked how she is")

    def held(self, why: str) -> None:
        """The greeting couldn't be spoken this tick (a call, mid-turn).
        Logged once per reason per day, then retried quietly every tick:
        a row a minute would bury the table."""
        _, rows = self._today(self._now())
        reason = f"{_HELD}: {why}"
        if any(row["reason"] == reason for row in rows):
            return
        self._log(reason, spoken=False, suppressed_by=why)

    def away(self, step: Step) -> None:
        self._log(f"{_AWAY} ({step.detail})", spoken=False, suppressed_by="she said she is away")
        logger.info("quiet day: no word from her, but she said she is away %s", step.detail)

    def notify(self, step: Step) -> int:
        """Tells every paired family phone, once. Returns how many it
        reached. Logged either way, so a failed send isn't retried every
        minute; the log line says why."""
        reached = 0
        why_not: str | None = None
        if self._tell_family is None:
            why_not = "the family app is off"
        else:
            try:
                reached = self._tell_family(
                    "Melody", lambda who: family_message(who, step.detail)
                )
            except Exception as exc:  # a push service down must not stop the loop
                logger.warning("quiet day: couldn't tell the family: %s", exc)
                why_not = f"sending failed: {type(exc).__name__}"
            if why_not is None and reached == 0:
                why_not = "no family phone is paired for notifications"
        self._log(
            f"{_NOTIFIED} (asked at {step.detail}, no answer)",
            spoken=False,
            suppressed_by=why_not,
        )
        if why_not is None:
            logger.info("quiet day: told %d family phone(s)", reached)
        else:
            logger.warning("quiet day: family not told: %s", why_not)
        return reached


def family_message(calls_her: str | None, asked_at: str) -> str:
    """Calm and factual: what Melody knows, nothing it guesses. `calls_her`
    is what that family member calls her ("Mum"), from pairing."""
    who = (calls_her or "").strip() or "She"
    return (
        f"{who} hasn't talked to Melody yet today. "
        f"Melody asked how she was at {asked_at} and heard no answer."
    )


# -- the tool -------------------------------------------------------------------

GOING_AWAY_DESCRIPTION = (
    "Record that she is going away, so Melody won't worry her family if she "
    "doesn't talk to Melody. Call it when she says she's going out for the day, "
    "staying somewhere for some days, or travelling. days: how many days from "
    "today she'll be away (1 = today only). Call with days=0 when she says "
    "she's back early."
)


def make_going_away_tool(store_path: Path, *, today: Callable[[], date] = date.today) -> Tool:
    path = Path(store_path)

    def _going_away(days: int) -> dict[str, Any]:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return {"status": "error", "detail": "days must be a number"}
        if not 0 <= days <= MAX_AWAY_DAYS:
            return {"status": "error", "detail": f"days must be 0 to {MAX_AWAY_DAYS}"}
        with IdentityStore(path) as store:
            if days == 0:
                write_preference(store, AWAY_KEY, "")
                return {"status": "ok", "away": False}
            until = today() + timedelta(days=days - 1)
            write_preference(store, AWAY_KEY, until.isoformat())
        return {"status": "ok", "away": True, "until": until.isoformat()}

    return Tool(
        name="going_away",
        schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 0, "maximum": MAX_AWAY_DAYS},
            },
            "required": ["days"],
        },
        permission="preferences",
        handler=_going_away,
    )


def today_state(store_path: Path, now: datetime | None = None) -> str | None:
    """The quiet-day check's outcome today, in a sentence for the family
    app, or None when there was nothing to check (she talked to Melody,
    or it isn't the check time yet)."""
    now = now or _local_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with IdentityStore(Path(store_path)) as store:
        rows = [
            row
            for row in store.read("initiatives", kind=KIND)
            if (row["reason"] or "").startswith(REASON_PREFIX) and _aware(row["ts"]) >= start
        ]
        turns = [_aware(row["ts"]) for row in store.read("turns")]
    if any(t >= start for t in turns):
        return None
    reasons = [row["reason"] for row in rows]
    away = next((r for r in reasons if r.startswith(_AWAY)), None)
    if away is not None:
        return f"She said she's away ({away[len(_AWAY):].strip(' ()')})."
    told = next((r for r in reasons if r.startswith(_NOTIFIED)), None)
    if told is not None:
        return "Melody asked how she was this morning and heard no answer."
    if any(r.startswith(_GREETED) for r in reasons):
        return "Melody has asked how she is; no answer yet."
    return None
