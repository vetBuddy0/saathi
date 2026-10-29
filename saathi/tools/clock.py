"""A clock on her screen, with timers and a stopwatch, by asking.

Why it exists: the owner asked (2026-10-29) for "a small clock Saathi
can pull up for timers, stopwatch etc". Asking the time, boiling an egg,
timing a walk: things a kitchen clock does, said instead of pressed.

`ClockController` holds what is on the clock and tells the face
(`{"type": "clock", ...}` through the same broadcast seam as the media
panel and cards); `static/js/clock-panel.js` draws it and counts the
seconds itself, so nothing ticks over the socket. The server's clock
tick (`screen/server.py`) asks `finished()` once a second and says
"Your tea timer is done" when she is free to hear it -- a timer that
ends mid-sentence waits for the sentence, the screen's chime does not.

The clock is content she asked for, beside the face like the phone
panel, not a status label under it (CLAUDE.md). It goes away when she
says so, on its close button, or `IDLE_HIDE` after nothing is left
running. Lost: a clock that stays up until told (it would hold half
the screen for the rest of the day after one "what time is it?"), and
alarms at a time of day, which are reminders (`tools/reminders.py`).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from saathi.tools.registry import Tool

PERMISSION = "clock"
MAX_TIMERS = 3
MAX_TIMER_SECONDS = 24 * 3600
IDLE_HIDE = 120.0  # seconds with nothing running before the clock goes
DONE_SHOWN = 60.0  # how long a finished timer stays up, "Time's up"

CLOCK_DESCRIPTION = (
    "The clock on her screen. `show` when she asks the time or for the clock "
    "(the result has the time to tell her); `hide` when she asks to close it. "
    "`start_timer` with `minutes` and/or `seconds` (and a `label` if she named "
    "what it is for, e.g. \"tea\"); `cancel_timer` stops one (by label) or all. "
    "`stopwatch_start`, `stopwatch_stop`, `stopwatch_reset` for a stopwatch. "
    "The clock shows itself for timers and the stopwatch."
)

ACTIONS = (
    "show",
    "hide",
    "start_timer",
    "cancel_timer",
    "stopwatch_start",
    "stopwatch_stop",
    "stopwatch_reset",
)


@dataclass
class Timer:
    id: str
    label: str
    seconds: int
    ends_at: float  # wall clock, epoch seconds
    done_at: float | None = None
    announced: bool = False


def duration_words(seconds: int, before_a_noun: bool = False) -> str:
    """"5 minutes", "1 hour 30 minutes", "45 seconds"; before a noun,
    "5-minute" ("your 5-minute timer")."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    for value, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")):
        if value and before_a_noun:
            parts.append(f"{value}-{unit}")
        elif value:
            parts.append(f"{value} {unit}{'' if value == 1 else 's'}")
    return " ".join(parts) or "0 seconds"


class ClockController:
    """Thread-safe: the tool runs on the turn's executor thread, the
    tick and the face's taps on the loop."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._broadcast: Callable[[dict], None] | None = None
        self.visible = False
        self.timers: list[Timer] = []
        self._stopwatch_elapsed = 0.0
        self._stopwatch_since: float | None = None
        self._stopwatch_used = False
        self._hide_at: float | None = None

    # -- the seam ---------------------------------------------------------

    def set_broadcast(self, broadcast: Callable[[dict], None] | None) -> None:
        self._broadcast = broadcast

    def now(self) -> float:
        """The clock's own time, which its timers' `ends_at` are on."""
        return self._clock()

    def message(self) -> dict[str, Any]:
        with self._lock:
            return self._message()

    def _message(self) -> dict[str, Any]:
        stopwatch = None
        if self._stopwatch_used:
            stopwatch = {
                "running": self._stopwatch_since is not None,
                "elapsed_ms": round(self._stopwatch_elapsed * 1000),
                "since_ms": None
                if self._stopwatch_since is None
                else round(self._stopwatch_since * 1000),
            }
        return {
            "type": "clock",
            "visible": self.visible,
            "now_ms": round(self._clock() * 1000),
            "timers": [
                {
                    "id": t.id,
                    "label": t.label,
                    "seconds": t.seconds,
                    "ends_at_ms": round(t.ends_at * 1000),
                    "done": t.done_at is not None,
                }
                for t in self.timers
            ],
            "stopwatch": stopwatch,
        }

    def _emit(self) -> None:
        if self._broadcast is not None:
            self._broadcast(self._message())

    def _running(self) -> bool:
        return any(t.done_at is None for t in self.timers) or self._stopwatch_since is not None

    def _changed(self, now: float) -> None:
        self._hide_at = None if self._running() else now + IDLE_HIDE
        self._emit()

    # -- what she asks for ------------------------------------------------

    def show(self) -> None:
        with self._lock:
            self.visible = True
            self._changed(self._clock())

    def hide(self) -> None:
        with self._lock:
            self.visible = False
            self.timers = [t for t in self.timers if t.done_at is None]
            self._emit()

    def start_timer(self, seconds: int, label: str = "") -> Timer:
        with self._lock:
            now = self._clock()
            active = [t for t in self.timers if t.done_at is None]
            if len(active) >= MAX_TIMERS:
                raise ValueError(f"at most {MAX_TIMERS} timers at once")
            timer = Timer(uuid.uuid4().hex[:8], label.strip(), int(seconds), now + int(seconds))
            self.timers.append(timer)
            self.visible = True
            self._changed(now)
            return timer

    def cancel_timer(self, label: str = "") -> list[Timer]:
        wanted = label.strip().lower()
        with self._lock:
            gone = [
                t for t in self.timers if not wanted or t.label.lower() == wanted
            ]
            self.timers = [t for t in self.timers if t not in gone]
            self._changed(self._clock())
            return gone

    def dismiss(self, timer_id: str) -> bool:
        """The Stop on a finished timer (a tap)."""
        with self._lock:
            before = len(self.timers)
            self.timers = [t for t in self.timers if t.id != timer_id]
            if len(self.timers) == before:
                return False
            self._changed(self._clock())
            return True

    def stopwatch_start(self) -> None:
        with self._lock:
            now = self._clock()
            if self._stopwatch_since is None:
                self._stopwatch_since = now
            self._stopwatch_used = True
            self.visible = True
            self._changed(now)

    def stopwatch_stop(self) -> float:
        with self._lock:
            now = self._clock()
            if self._stopwatch_since is not None:
                self._stopwatch_elapsed += now - self._stopwatch_since
                self._stopwatch_since = None
            self._changed(now)
            return self._stopwatch_elapsed

    def stopwatch_reset(self) -> None:
        with self._lock:
            self._stopwatch_elapsed = 0.0
            self._stopwatch_since = None
            self._stopwatch_used = False
            self._changed(self._clock())

    # -- the server's tick ----------------------------------------------------

    def finished(self) -> list[Timer]:
        """Timers that have just run out, each returned once (to be
        said); also drops finished ones shown long enough, and hides an
        idle clock. Called once a second on the loop."""
        with self._lock:
            now = self._clock()
            changed = False
            ended = []
            for timer in self.timers:
                if timer.done_at is None and timer.ends_at <= now:
                    timer.done_at = now
                    self.visible = True  # closed while it ran: back up to show it
                    changed = True
                if timer.done_at is not None and not timer.announced:
                    timer.announced = True
                    ended.append(timer)
            kept = [t for t in self.timers if t.done_at is None or now - t.done_at < DONE_SHOWN]
            if len(kept) != len(self.timers):
                self.timers = kept
                changed = True
            if changed:
                self._hide_at = None if self._running() else now + IDLE_HIDE
            idle = self._hide_at is not None and now >= self._hide_at and not self.timers
            if self.visible and idle:
                self.visible = False
                changed = True
            if changed:
                self._emit()
            return ended


def done_line(timers: list[Timer]) -> str:
    parts = []
    for timer in timers:
        if timer.label:
            parts.append(f"The {timer.label} timer is done.")
        else:
            parts.append(f"Your {duration_words(timer.seconds, before_a_noun=True)} timer is done.")
    return " ".join(parts)


def make_clock_tool(clock: ClockController) -> Tool:
    def _clock(
        action: str,
        minutes: float | None = None,
        seconds: float | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        now_words = datetime.now().strftime("%H:%M, %A %d %B")
        if action == "show":
            clock.show()
            return {"status": "ok", "time": now_words}
        if action == "hide":
            clock.hide()
            return {"status": "ok"}
        if action == "start_timer":
            total = round((minutes or 0) * 60 + (seconds or 0))
            if total <= 0 or total > MAX_TIMER_SECONDS:
                return {"status": "needs_duration", "detail": "ask her how long"}
            try:
                timer = clock.start_timer(total, label or "")
            except ValueError as exc:
                return {"status": "error", "detail": str(exc)}
            return {"status": "ok", "timer": duration_words(timer.seconds), "label": timer.label}
        if action == "cancel_timer":
            gone = clock.cancel_timer(label or "")
            return {"status": "ok" if gone else "not_found", "cancelled": len(gone)}
        if action == "stopwatch_start":
            clock.stopwatch_start()
            return {"status": "ok"}
        if action == "stopwatch_stop":
            elapsed = clock.stopwatch_stop()
            return {"status": "ok", "elapsed": duration_words(round(elapsed))}
        if action == "stopwatch_reset":
            clock.stopwatch_reset()
            return {"status": "ok"}
        return {"status": "error", "detail": f"action is one of {list(ACTIONS)}"}

    return Tool(
        name="clock",
        schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "minutes": {"type": "number"},
                "seconds": {"type": "number"},
                "label": {"type": "string"},
            },
            "required": ["action"],
        },
        permission=PERMISSION,
        handler=_clock,
    )
