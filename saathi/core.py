"""The state machine, and the only thing in this codebase that sets state.

Everything downstream — the face, the log, eventually initiative's gate —
reacts to a state `core.py` already decided. Nothing else is allowed to
assign state directly, including the voice engine: it emits intent, `core.py`
decides what that does to the state machine, the tool executes. See "Reaching
through one [interface] ... silently removes the architecture" in CLAUDE.md.

Checkpoint 1 has no audio pipeline and no voice engine, so the only real
event source is the spacebar (relayed through `screen/server.py`). The
transition table below is the full one from SPEC.md — states and edges the
wake-word path, the AI response path, and initiative will eventually drive —
because a partial table would have to be rewritten rather than extended at
checkpoint 2. Everything except `press`/`release` is exercised with
synthetic events in tests until those sources exist.

Contested decision: a physical button press wakes *and* confirms in one
step (`SLEEPING`/`IDLE` + `press` -> `LISTENING` directly), skipping
`ATTENTIVE`. `ATTENTIVE` is for the wake-word path, where the device heard
something and has to work out if it was addressed — a deliberate button
press already answers that question, so routing it through `ATTENTIVE` too
would add a hop with nothing to decide. `ATTENTIVE` is reachable in v1 only
via the (fake, for now) `notice` event.

Contested decision: an event with no matching transition for the current
state is a no-op, not an error. A device meant to sit in someone's home
should not crash because a stray key or a race delivered an event out of
order; tests can still assert on the exact table below.

Checkpoint 2 adds `(SPEAKING, "barge_in") -> LISTENING`: if she starts
talking while Saathi is speaking, that is not "wait for the sentence to
finish, then listen" — it is a new turn, immediately. `THINKING`'s "no_response"
still gets there via `IDLE`, unchanged; barge-in only shortcuts `SPEAKING`,
because that is the one state where staying in it after she has started
talking is actively rude, not just slow. The audio-side half of this —
noticing she started talking, stopping playback — lives in
`audio/vad.py` and `audio/playback.py`; `core.py` only owns what happens
to the state once that event arrives, same as every other event here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class State(str, Enum):
    SLEEPING = "sleeping"
    IDLE = "idle"
    ATTENTIVE = "attentive"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class Event:
    """A thing that happened. `meta` carries event-specific detail (e.g.
    which tool triggered a handoff) without widening this dataclass."""

    kind: str
    meta: dict = field(default_factory=dict)


Observer = Callable[[State, Event], None]

# Wildcard source state: checked when there is no exact (state, kind) match.
_ANY = None

_TRANSITIONS: dict[tuple[State | None, str], State] = {
    (State.SLEEPING, "press"): State.LISTENING,
    (State.SLEEPING, "wake"): State.IDLE,
    (State.IDLE, "press"): State.LISTENING,
    (State.IDLE, "notice"): State.ATTENTIVE,
    (State.ATTENTIVE, "confirm"): State.LISTENING,
    (State.ATTENTIVE, "dismiss"): State.IDLE,
    (State.LISTENING, "release"): State.THINKING,
    (State.THINKING, "response_ready"): State.SPEAKING,
    (State.THINKING, "no_response"): State.IDLE,
    (State.SPEAKING, "done"): State.IDLE,
    (State.SPEAKING, "barge_in"): State.LISTENING,
    (State.HANDOFF, "resolved"): State.THINKING,
    (_ANY, "idle_timeout"): State.SLEEPING,
    (_ANY, "handoff"): State.HANDOFF,
}


class Core:
    """Owns the current `State` and the transition table above.

    Not thread-safe by design: `handle()` is meant to be called from a
    single event loop (the screen server's), matching "compiling context
    happens between turns, never during one" — one place decides, in order.
    """

    def __init__(self, initial: State = State.SLEEPING) -> None:
        self._state = initial
        self._observers: list[Observer] = []

    @property
    def state(self) -> State:
        return self._state

    def subscribe(self, observer: Observer) -> Callable[[], None]:
        """Register `observer(new_state, event)`, called after a transition
        that actually changes state. Returns an unsubscribe callable."""
        self._observers.append(observer)

        def unsubscribe() -> None:
            self._observers.remove(observer)

        return unsubscribe

    def handle(self, event: Event) -> State:
        """Apply `event` to the current state. Returns the resulting state,
        whether or not it changed."""
        new_state = _TRANSITIONS.get((self._state, event.kind))
        if new_state is None:
            new_state = _TRANSITIONS.get((_ANY, event.kind))
        if new_state is None or new_state == self._state:
            return self._state

        self._state = new_state
        for observer in list(self._observers):
            observer(new_state, event)
        return self._state
