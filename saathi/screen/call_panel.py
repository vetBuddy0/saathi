"""The phone panel on the right of the screen while a call is on.

Exists because a call was invisible: the face looked exactly as it
does between turns while a phone was ringing somewhere, and the only
way to hang up was a two-second spacebar hold she had to be told about.
The panel shows who is on the line, their number, how long the call
has been going and whether it has been answered, plus one big red
button that ends it.

What crosses the socket is `{"type": "call", "call": {...} | null}`.
`call/controller.py` (the single owner of call state) reports changes
through `update()`; this object turns them into that message and keeps
the last one so a reload, or a second screen, gets it on connect -- the
same rule cards follow. The browser counts the timer itself from
`elapsed_seconds`, so nothing ticks over the socket once a second.

The button does not hang up by itself. A tap arrives at the server as
`{"type": "call_hangup", "id": ...}`; the server checks the id against
`current_id` (a stale tap from an earlier call is dropped) and then
fires the hold seam's registered handler, the same `controller.hangup`
a two-second spacebar hold reaches. No second hang-up path exists.

Contested: the status word ("Calling", "Ringing", "Connected"). CLAUDE.md
forbids status text *under the face*; this is a phone's own screen
beside it, and the owner asked for it. The option that lost was no word
at all (a timer only once connected): before the far end answers, a
blank panel reads as broken.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

Broadcast = Callable[[dict], None]

STATUSES = ("calling", "ringing", "connected")


@dataclass(frozen=True)
class CallView:
    """What the screen may know about the call in progress. `number` is
    for her eyes only: never logged, never put in a model's context."""

    name: str
    number: str
    status: str  # one of STATUSES
    connected_at: float | None = None  # monotonic; set once answered


class CallPanel:
    def __init__(
        self,
        broadcast: Broadcast | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._broadcast = broadcast
        self._clock = clock
        self._lock = threading.Lock()
        self._view: CallView | None = None
        self._id: str | None = None

    def set_broadcast(self, broadcast: Broadcast | None) -> None:
        with self._lock:
            self._broadcast = broadcast

    @property
    def current_id(self) -> str | None:
        return self._id

    def update(self, view: CallView | None) -> None:
        """Called by the controller on every visible change. Any thread;
        never blocks (the controller calls it under its own lock)."""
        if view is not None and view.status not in STATUSES:
            raise ValueError(f"unknown call status: {view.status!r}")
        with self._lock:
            if view is None:
                if self._view is None:
                    return
                self._view = None
                self._id = None
            else:
                if self._view is None:
                    self._id = uuid.uuid4().hex
                if view == self._view:
                    return
                self._view = view
            message = self._message_locked()
            broadcast = self._broadcast
        if broadcast is not None:
            broadcast(message)

    def message(self) -> dict | None:
        """The panel as it should be drawn right now (for a fresh
        connection), or None when there is no call."""
        with self._lock:
            if self._view is None:
                return None
            return self._message_locked()

    def _message_locked(self) -> dict:
        view = self._view
        if view is None:
            return {"type": "call", "call": None}
        elapsed = None
        if view.status == "connected" and view.connected_at is not None:
            elapsed = max(0.0, round(self._clock() - view.connected_at, 1))
        return {
            "type": "call",
            "call": {
                "id": self._id,
                "name": view.name,
                "number": view.number,
                "status": view.status,
                "elapsed_seconds": elapsed,
            },
        }
