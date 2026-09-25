"""On-screen cards for resolving ambiguity by tap or voice — the five
primitives (Choice, Confirm, Read-back, Holding, Entry) and the
controller that shows exactly one of them at a time.

Entry is the fifth, added 2026-09-26 at the user's request: saving a
contact shows the number on a dialpad she can correct by tapping, and
the name, both editable before she says yes. The brief said four; a
read-back with Yes/No made her say the whole number again to fix one
digit. Its answers come in two flavours: edits (`{"number": …}` /
`{"name": …}`) update the card in place with the same id, like a
holding card advancing; a decision (`yes`/`dismiss`) clears it as any
card. Nobody waiting on the card is told about an edit -- the values
travel with the yes.

Why this exists as a module in `screen/` rather than inside each tool
that needs it: a card is the one thing on the screen that asks her
something, and every stream that asks (calling, music, reminders) must
ask the same way — three numbered options at most, spoken as well as
shown, tap or voice both answering, a large way out — or the device has
several dialects of "which one?" and she has to learn each. Streams
import this; they don't draw.

Cards are content and interaction, not status. SPEC.md forbids status
text under the face ("Listening…"); a card is a question she is being
asked, with her options, and the answer is hers to give. Someone will
read "no text on screen" and want to strip this; the distinction is the
whole point of the rule — a label describes the device, a card addresses
her.

Rules the code enforces, not just documents: three options maximum
(`choice()` raises on a fourth — the caller must pick the best three and
say so out loud, which the module can't do for it); every card carries
`spoken`; one card at a time (`show()` replaces); nothing here has a
timer — a card stays until answered, dismissed or replaced; every
non-holding card can be dismissed.

Threading: `show()`/`clear()`/`answer()` are called from the executor
thread (a tool inside `end_turn()`) and from the event-loop thread (the
WebSocket handler on a tap). A lock keeps the current card consistent;
the broadcast goes through the seam `server.py` installs, which hops to
the loop. `ask()` blocks the calling thread until the card is answered.
It must not be called inside `end_turn()`: the turn would sit in
THINKING with the mic closed, so she could only answer by tap, which
breaks "voice and touch both work". Tools show the card and return a
note telling the model what to say; her spoken answer arrives on the
next turn and the tool calls `answer()`. `ask()` is for code that runs
between turns (initiative, a call's own state machine).

Rejected: a component library. None is built for a 75-year-old across a
room; the target sizes, the absence of hover and the one-at-a-time rule
would all be fought rather than given.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable

MAX_OPTIONS = 3
ORDINALS = {1: "One", 2: "Two", 3: "Three"}

KINDS = ("choice", "confirm", "readback", "holding", "entry")


class TooManyOptions(ValueError):
    """More than three. Not truncated here on purpose: the rule is "say
    so and offer the best three", and which three are best is the
    caller's call, as is saying so."""


@dataclass(frozen=True)
class Option:
    n: int  # 1-based; the number she hears and taps
    label: str

    @property
    def spoken(self) -> str:
        return f"{ORDINALS[self.n]}: {self.label}"


@dataclass(frozen=True)
class Card:
    kind: str
    title: str
    spoken: str  # what the engine says when the card appears; never empty
    options: tuple[Option, ...] = ()
    value: str | None = None  # readback: the grouped digits / short value
    progress: float | None = None  # holding: 0..1
    # readback only: the value is a question ("is that right?") answered
    # yes/no by tap or voice, not just acknowledged. See readback().
    confirmable: bool = False
    # entry only: the number as shown (grouped) and the name, both editable.
    number: str | None = None
    name: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "spoken": self.spoken,
        }
        if self.kind == "choice":
            message["options"] = [{"n": o.n, "label": o.label} for o in self.options]
        if self.value is not None:
            message["value"] = self.value
        if self.progress is not None:
            message["progress"] = self.progress
        if self.kind == "readback" and self.confirmable:
            message["confirm"] = True
        if self.kind == "entry":
            message["number"] = self.number or ""
            message["name"] = self.name or ""
        return message


# -- builders -------------------------------------------------------------


def choice(title: str, options: list[str] | tuple[str, ...], spoken: str | None = None) -> Card:
    """Two or three numbered options. `spoken` defaults to the title
    followed by each option as "One: …" — exactly what is on screen."""
    labels = [str(label).strip() for label in options if str(label).strip()]
    if len(labels) > MAX_OPTIONS:
        raise TooManyOptions(f"{len(labels)} options; the most a card may carry is {MAX_OPTIONS}")
    if len(labels) < 2:
        raise ValueError("a choice needs two or three options; use confirm() for one")
    opts = tuple(Option(n=i + 1, label=label) for i, label in enumerate(labels))
    if spoken is None:
        spoken = f"{title.strip()} " + " ".join(f"{o.spoken}." for o in opts)
    return Card(kind="choice", title=title.strip(), spoken=spoken.strip(), options=opts)


def confirm(statement: str, spoken: str | None = None) -> Card:
    """One statement, yes or no."""
    statement = statement.strip()
    return Card(kind="confirm", title=statement, spoken=(spoken or statement).strip())


def readback(
    title: str,
    value: str,
    spoken: str | None = None,
    *,
    confirm: bool = False,
    group: bool = True,
) -> Card:
    """A single value shown very large. Digits are grouped for the eye;
    `spoken` defaults to the digits read one at a time with a pause at
    each group, which is how a person reads a number back.

    `confirm=True` makes the read-back a question: Yes/No on screen,
    `{"yes": bool}` accepted by `answer()`. Added at reconciliation for
    Calling, whose read-back before saving a number IS the question --
    the option that lost was a Confirm card carrying the number as its
    statement, which drops the very-large grouped digits a read-back
    exists for. Off by default: a plain read-back is only acknowledged.

    `group=False`: the caller has grouped the value itself and knows its
    structure ("+65 9123 4567" -- country code apart); regrouping in threes
    would give "+659 123 4567". Added at reconciliation for Calling;
    `group_digits` itself keeps normalising, as its own tests require."""
    grouped = group_digits(value) if group else " ".join(str(value).split())
    if spoken is None:
        spoken = f"{title.strip()} {speak_value(grouped)}"
    return Card(
        kind="readback",
        title=title.strip(),
        spoken=spoken.strip(),
        value=grouped,
        confirmable=confirm,
    )


def holding(label: str, progress: float = 0.0, card_id: str | None = None) -> Card:
    """Progress while a button is held. Re-issued with the same id as
    the hold advances so the screen updates in place."""
    progress = max(0.0, min(1.0, float(progress)))
    card = Card(kind="holding", title=label.strip(), spoken=label.strip(), progress=progress)
    if card_id is not None:
        card = Card(**{**card.__dict__, "id": card_id})
    return card


def entry(title: str, *, number: str, name: str, spoken: str | None = None) -> Card:
    """A number on a dialpad and a name, both hers to change before she
    says yes. The number is shown grouped exactly as given (the caller
    knows "+65 9123 4567" has a country code; this module doesn't).

    Name editing is by voice, not an on-screen keyboard: 26 keys that
    fit beside the face would each be far under the 100px floor, and
    spelling by tap is the thing the audience finds hardest. She taps
    "Change name" and says it; the flow behind the card sets it through
    `answer()`."""
    shown = " ".join(str(number).split())
    name = " ".join(str(name).split())
    if spoken is None:
        spoken = (
            f"{title.strip()} {speak_value(shown)}, and the name, {name}. "
            "Say yes to save, or change the number or the name."
        )
    return Card(kind="entry", title=title.strip(), spoken=spoken.strip(), number=shown, name=name)


_RAW_NUMBER = re.compile(r"^\+?\d{0,20}$")


def regroup_number(shown: str, raw: str) -> str:
    """The display for an edited number: the grouping she was shown is
    kept for every leading digit that is unchanged, and digits added
    after that go in fours -- what a tap on the dialpad does, so the
    browser (cards.js, same function) and the server agree without the
    screen knowing what a country code is."""
    kept: list[str] = []
    i = 0
    for ch in shown:
        if ch == " ":
            kept.append(ch)
            continue
        if i < len(raw) and raw[i] == ch:
            kept.append(ch)
            i += 1
        else:
            break
    text = "".join(kept).rstrip()
    for ch in raw[i:]:
        last = text.rsplit(" ", 1)[-1] if text else ""
        if text and len(last.lstrip("+")) >= 4:
            text += " "
        text += ch
    return text


# A phone-number shape: digits with the spacing/punctuation numbers are
# written with. A "." or ":" means a decimal or a time ("37.5",
# "10:30"), which must pass through untouched -- found in review:
# "37.5" was becoming "375".
_DIGITS_ONLY = re.compile(r"^\+?[\d\s\-()]+$")


def group_digits(value: str) -> str:
    """"0412345678" -> "041 234 5678". Anything that isn't a phone-number
    shape (a decimal, a time, a name) passes through untouched. Groups
    of three from the left; a trailing single digit joins the group
    before it (no "… 567 8")."""
    text = str(value).strip()
    if not text or not _DIGITS_ONLY.match(text):
        return text
    prefix = "+" if text.startswith("+") else ""
    digits = re.sub(r"\D", "", text)
    if len(digits) <= 4:
        return prefix + digits
    groups = [digits[i : i + 3] for i in range(0, len(digits), 3)]
    if len(groups[-1]) == 1:
        last = groups.pop()
        groups[-1] += last
    return prefix + " ".join(groups)


def speak_value(grouped: str) -> str:
    """Digits one at a time, a comma between groups; anything else as is."""
    if not _DIGITS_ONLY.match(grouped):
        return grouped
    parts = []
    for group in grouped.split():
        if group.startswith("+"):
            # "plus" is a word, not four letters -- found in review.
            parts.append(("plus " + " ".join(group[1:])).strip())
        else:
            parts.append(" ".join(group))
    return ", ".join(parts)


# -- the controller ------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    card_id: str
    kind: str
    # {"choice": n} | {"yes": bool} | {"dismiss": True}
    # entry: {"yes": True, "number": raw, "name": str} carries her edited values
    answer: dict[str, Any]
    source: str  # "tap" | "voice" | "hold" | "code"
    card: Card

    @property
    def choice(self) -> int | None:
        return self.answer.get("choice")

    @property
    def yes(self) -> bool | None:
        return self.answer.get("yes")

    @property
    def dismissed(self) -> bool:
        return bool(self.answer.get("dismiss"))


Broadcast = Callable[[dict[str, Any]], None]
AnswerCallback = Callable[[Answer], None]


def validate_answer(card: Card, answer: Any) -> dict[str, Any] | None:
    """The one accepted shape per kind, or None. Everything else -- a
    stray key, a fourth option, a string "yes" -- is dropped, never
    guessed at."""
    if not isinstance(answer, dict):
        return None
    if answer.get("dismiss") is True:
        return {"dismiss": True}
    if card.kind == "choice":
        n = answer.get("choice")
        if isinstance(n, bool) or not isinstance(n, int):
            return None
        if 1 <= n <= len(card.options):
            return {"choice": n}
        return None
    if card.kind == "confirm":
        yes = answer.get("yes")
        if isinstance(yes, bool):
            return {"yes": yes}
        return None
    if card.kind == "readback" and card.confirmable:
        yes = answer.get("yes")
        if isinstance(yes, bool):
            return {"yes": yes}
        return None
    if card.kind == "entry":
        return _validate_entry(card, answer)
    return None  # a plain readback and holding only take dismiss


def _raw_number(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = "".join(value.split())
    return raw if _RAW_NUMBER.match(raw) else None


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    return name or None


def _validate_entry(card: Card, answer: dict[str, Any]) -> dict[str, Any] | None:
    """Yes carries the values to save (hers if given, else the card's
    current ones -- a spoken "yes" doesn't repeat them); no is just no;
    a number and/or name without a decision is an edit."""
    if "yes" in answer:
        yes = answer["yes"]
        if not isinstance(yes, bool):
            return None
        if yes is False:
            return {"yes": False}
        number = _raw_number(answer.get("number", card.number or ""))
        name = _clean_name(answer.get("name", card.name or ""))
        if number is None or name is None:
            return None
        return {"yes": True, "number": number, "name": name}
    edit: dict[str, Any] = {}
    if "number" in answer:
        number = _raw_number(answer["number"])
        if number is None:
            return None
        edit["number"] = number
    if "name" in answer:
        name = _clean_name(answer["name"])
        if name is None:
            return None
        edit["name"] = name
    return edit or None


def is_edit(answer: dict[str, Any]) -> bool:
    """An entry card's number and/or name without a decision."""
    return bool(answer) and set(answer) <= {"number", "name"}


class CardController:
    """Holds the one current card and routes its answer.

    `answer()` is the single entry point for both ways of answering:
    the WebSocket handler calls it on a tap (`source="tap"`), a tool
    calls it when she says "the second one" (`source="voice"`). Either
    clears the card and wakes every `on_answer` callback and any
    `ask()` waiting on it."""

    def __init__(self, broadcast: Broadcast | None = None) -> None:
        self._broadcast = broadcast
        self._lock = threading.Lock()
        self._current: Card | None = None
        self._callbacks: list[AnswerCallback] = []
        self._waiters: dict[str, tuple[threading.Event, list[Answer]]] = {}

    def set_broadcast(self, broadcast: Broadcast | None) -> None:
        self._broadcast = broadcast

    @property
    def current(self) -> Card | None:
        return self._current

    def _emit(self, card: Card | None) -> None:
        if self._broadcast is None:
            return
        self._broadcast({"type": "card", "card": card.as_message() if card else None})

    def show(self, card: Card) -> str:
        """Show `card`, replacing whatever was up. One at a time, never
        stacked: a replaced card's `ask()` is released with a dismiss
        so nothing waits on a question she can no longer see."""
        if card.kind not in KINDS:
            raise ValueError(f"unknown card kind {card.kind!r}")
        if not card.spoken:
            raise ValueError("every card must have something to say")
        # The broadcast happens under the lock so the screen is told
        # in the same order `_current` changed -- two shows racing from
        # the executor and loop threads must not leave the screen on a
        # card whose taps are then rejected as stale (found in review).
        # Callbacks run outside it: one may show() a card of its own.
        with self._lock:
            previous = self._current
            self._current = card
            replaced = None
            if previous is not None and previous.id != card.id:
                replaced = self._release(previous, {"dismiss": True}, "code")
            self._emit(card)
        if replaced is not None:
            self._notify(replaced)
        return card.id

    def clear(self) -> None:
        with self._lock:
            previous = self._current
            self._current = None
            released = self._release(previous, {"dismiss": True}, "code") if previous else None
            self._emit(None)
        if released is not None:
            self._notify(released)

    def answer(self, card_id: str, answer: Any, *, source: str = "tap") -> bool:
        """True if `card_id` is the current card and `answer` is a valid
        shape for it; the card is then cleared and everyone waiting is
        told. False (and nothing happens) for a stale id -- a tap on a
        card that was already replaced -- or a malformed answer."""
        with self._lock:
            card = self._current
            if card is None or card.id != card_id:
                return False
            valid = validate_answer(card, answer)
            if valid is None:
                return False
            if is_edit(valid):
                # An entry card edited, not decided: same id, new values,
                # still up. Nobody waiting is told; the yes carries them.
                updated = replace(
                    card,
                    number=regroup_number(card.number or "", valid["number"])
                    if "number" in valid
                    else card.number,
                    name=valid.get("name", card.name),
                )
                self._current = updated
                self._emit(updated)
                return True
            self._current = None
            result = self._release(card, valid, source)
            self._emit(None)
        self._notify(result)
        return True

    def on_answer(self, callback: AnswerCallback) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _release(self, card: Card, answer: dict[str, Any], source: str) -> Answer:
        """Under the lock: build the Answer and wake an ask() waiter."""
        result = Answer(card_id=card.id, kind=card.kind, answer=answer, source=source, card=card)
        waiter = self._waiters.pop(card.id, None)
        if waiter is not None:
            event, slot = waiter
            slot.append(result)
            event.set()
        return result

    def _notify(self, result: Answer) -> None:
        for callback in list(self._callbacks):
            callback(result)

    def ask(self, card: Card, timeout: float | None = None) -> Answer | None:
        """Show `card` and block until it is answered, dismissed or
        replaced. No timeout by default -- nothing on a card times out.
        Not for use inside `end_turn()` (see the module docstring).
        Returns None only if a `timeout` was given and passed; the card
        is then cleared."""
        event = threading.Event()
        slot: list[Answer] = []
        with self._lock:
            self._waiters[card.id] = (event, slot)
        self.show(card)
        if not event.wait(timeout):
            with self._lock:
                self._waiters.pop(card.id, None)
                still_up = self._current is not None and self._current.id == card.id
                if still_up:
                    self._current = None
            if still_up:
                self._emit(None)
            return None
        return slot[0]

    async def ask_async(self, card: Card, timeout: float | None = None) -> Answer | None:
        """`ask()` for code on the event loop (initiative, a call's own
        loop-side state machine)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.ask, card, timeout)


# -- hold-to-confirm ----------------------------------------------------------

HoldComplete = Callable[[], None]


class HoldController:
    """The spacebar as a hold-to-confirm button (a call's hang-up).

    While a handler is set, `server.py` routes press/release here
    instead of to `core.py`: a short press does nothing at all -- no
    turn, no capture -- and a press held for `seconds` fires
    `on_complete()` exactly once. The Holding card shows the progress
    of the hold so she can see it is doing something and let go if she
    didn't mean it. The timer lives in the server (it owns the loop);
    this object only knows how far along the hold is and whether it
    has fired.

    Why the press is taken away from the state machine entirely rather
    than passed through: during a call the mic is already open to the
    other person, and a press starting a turn would talk over the call.
    The stream that sets the handler is saying "for now, the button
    means this"."""

    def __init__(self, cards: CardController) -> None:
        self._cards = cards
        self._handler: HoldComplete | None = None
        self.seconds = 2.0
        self.label = "Keep holding"
        self._card_id: str | None = None
        self._fired = False

    def set_handler(
        self, on_complete: HoldComplete, *, seconds: float = 2.0, label: str = "Keep holding"
    ) -> None:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        self._handler = on_complete
        self.seconds = float(seconds)
        self.label = label.strip() or "Keep holding"

    def clear(self) -> None:
        self._handler = None
        self.abandon()

    @property
    def active(self) -> bool:
        return self._handler is not None

    @property
    def holding(self) -> bool:
        return self._card_id is not None

    def begin(self) -> None:
        """The press. Shows the Holding card at 0."""
        self._fired = False
        card = holding(self.label, 0.0)
        self._card_id = card.id
        self._cards.show(card)

    def tick(self, elapsed: float) -> bool:
        """Called by the server's timer. Updates the card; at `seconds`
        fires the handler once and clears the card. Returns True once
        complete."""
        if self._card_id is None or self._fired:
            return self._fired
        progress = min(1.0, elapsed / self.seconds)
        if progress >= 1.0:
            self._fired = True
            handler = self._handler
            card_id = self._card_id
            self._card_id = None
            current = self._cards.current
            if current is not None and current.id == card_id:
                self._cards.clear()
            if handler is not None:
                handler()
            return True
        self._cards.show(holding(self.label, progress, card_id=self._card_id))
        return False

    def abandon(self) -> None:
        """The release before `seconds`: the card goes, nothing fires."""
        card_id = self._card_id
        self._card_id = None
        if card_id is None:
            return
        current = self._cards.current
        if current is not None and current.id == card_id:
            self._cards.clear()
