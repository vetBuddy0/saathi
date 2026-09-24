"""The cards calling needs from the screen — a *stub* shaped exactly like
SCREEN's real module, so the swap at merge is an import change.

The real interface is `saathi/screen/cards.py` on branch `batch/youtube`
(commit 12fd854, PR #3), not yet on `main`. Calling must not import it
from there or build its own screen cards, so this file copies its
*shape* only: the builders `choice(title, labels, spoken=None)` (2-3
labels; 4+ raises `TooManyOptions`), `confirm(statement, spoken=None)`,
`readback(title, value, spoken=None)`; a `CardController` with
`show(card) -> id`, `clear()`, `answer(id, payload, *, source) -> bool`,
`on_answer(callback) -> unsubscribe`; and `Answer` with `.card_id .kind
.choice .yes .dismissed .source`. At merge: replace
`from saathi.call.cards import ...` with
`from saathi.screen.cards import ...` and delete this file.

The one rule taken from SCREEN's design and enforced by how calling uses
it: **never `ask()` inside a tool handler.** `ask()` blocks, and a tool
handler runs inside `end_turn()` — blocking there holds the face in
THINKING with the mic closed, so she could never answer. Calling
`show()`s a card, returns its `spoken` text as the tool's note (that is
what she hears), and her spoken answer arrives as the *next* turn's tool
call, which feeds `answer(..., source="voice")`. A tap arrives through
the screen the same way. Either way `on_answer` callbacks see it.

Contested: SCREEN's `readback()` spells digits for speech by itself.
Calling passes its own `spoken` anyway, because the read-back must also
*say* an inferred country code ("that's a Singapore number, plus six
five") — the brief's "infer the country code, but never silently".
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

_DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_ids = itertools.count(1)


class TooManyOptions(ValueError):
    """A Choice card with 4+ options: more than she can hold in mind from
    hearing them once."""


@dataclass(frozen=True)
class Card:
    kind: str  # "choice" | "confirm" | "readback"
    title: str
    spoken: str
    options: tuple[str, ...] = ()
    value: str = ""
    id: str = field(default_factory=lambda: f"card-{next(_ids)}")


@dataclass(frozen=True)
class Answer:
    card_id: str
    kind: str
    choice: int | None = None
    yes: bool | None = None
    dismissed: bool = False
    source: str = "tap"  # "tap" | "voice"


def _ordinal(n: int) -> str:
    return ("first", "second", "third")[n]


def choice(title: str, labels: list[str], spoken: str | None = None) -> Card:
    if len(labels) > 3:
        raise TooManyOptions(f"{len(labels)} options; a Choice card holds at most 3")
    if len(labels) < 2:
        raise ValueError("a Choice card needs at least 2 options")
    if spoken is None:
        spoken = f"{title} " + ", ".join(
            f"the {_ordinal(i)} is {label}" for i, label in enumerate(labels)
        ) + "?"
    return Card(kind="choice", title=title, spoken=spoken, options=tuple(labels))


def confirm(statement: str, spoken: str | None = None) -> Card:
    return Card(kind="confirm", title=statement, spoken=spoken or statement)


def readback(title: str, value: str, spoken: str | None = None) -> Card:
    if spoken is None:
        digits = " ".join(_DIGIT_WORDS[int(c)] for c in value if c.isdigit())
        spoken = f"{title}: {digits}. Is that right?"
    return Card(kind="readback", title=title, spoken=spoken, value=value)


class CardController(Protocol):
    def show(self, card: Card) -> str: ...

    def clear(self) -> None: ...

    def answer(self, card_id: str, payload: dict, *, source: str = "tap") -> bool: ...

    def on_answer(self, callback: Callable[[Answer], None]) -> Callable[[], None]: ...


class FakeCardController:
    """Behaves like the real controller as far as calling can observe:
    one card at a time (`show` replaces), `answer` only for the card on
    screen, callbacks run synchronously inside `answer`."""

    def __init__(self) -> None:
        self.current: Card | None = None
        self.shown: list[Card] = []
        self._callbacks: list[Callable[[Answer], None]] = []
        self._lock = threading.Lock()

    def show(self, card: Card) -> str:
        with self._lock:
            self.current = card
            self.shown.append(card)
        return card.id

    def clear(self) -> None:
        with self._lock:
            self.current = None

    def answer(self, card_id: str, payload: dict, *, source: str = "tap") -> bool:
        with self._lock:
            card = self.current
            if card is None or card.id != card_id:
                return False
            self.current = None
            callbacks = list(self._callbacks)
        answer = Answer(
            card_id=card_id,
            kind=card.kind,
            choice=payload.get("choice"),
            yes=payload.get("yes"),
            dismissed=bool(payload.get("dismiss")),
            source=source,
        )
        for callback in callbacks:
            callback(answer)
        return True

    def on_answer(self, callback: Callable[[Answer], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            self._callbacks.remove(callback)

        return unsubscribe
