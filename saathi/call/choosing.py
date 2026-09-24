"""When the name she said could be more than one person: ask, with a
card, and dial only the one she picks.

Exists because the unsure band must never dial on its own. Two or three
close names go on a Choice card (numbered from 1, as on SCREEN's cards),
spoken aloud and answerable by "the first one"; a single plausible but
not confident name goes on a Confirm card ("Did you mean Priya?"). The
card is *shown* and its `spoken` returned as the tool's note — no
`ask()`, no waiting in a handler. Her answer comes back through
`on_answer`:

- by voice (`answer_card`, on the screen server's executor thread): the
  dial is deferred to `outcome()`, which that same tool call reads, so
  the tool can say "Calling Basudeb." — or that the call failed — in the
  same turn;
- by tap (the screen's event-loop thread): the dial runs on a short
  worker thread, because a Twilio request must not block the loop that
  has a 100 ms face budget.

Contested: a tap-chosen call rings without "Calling X." being said —
nothing can speak outside a turn without reaching through
`VoiceSession`. The card she just tapped shows the name; that stands in.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from saathi.call.cards import Answer, CardController, choice, confirm
from saathi.call.contacts import Contact

logger = logging.getLogger(__name__)

_ORDINALS = ("first", "second", "third")

Dial = Callable[[Contact], dict[str, Any]]


@dataclass(frozen=True)
class _Pending:
    options: tuple[Contact, ...]
    kind: str  # "choice" | "confirm"


class ChoiceFlow:
    def __init__(self, cards: CardController, dial: Dial) -> None:
        self._cards = cards
        self._dial = dial
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._to_dial: dict[str, Contact] = {}
        self._outcomes: dict[str, dict[str, Any]] = {}
        cards.on_answer(self._on_answer)

    def pending_card_ids(self) -> set[str]:
        with self._lock:
            return set(self._pending)

    def offer(self, said: str, options: list[Contact]) -> dict[str, Any]:
        if len(options) >= 2:
            names = [c.name for c in options[:3]]
            spoken = "Which one did you mean: " + ", ".join(
                f"the {_ORDINALS[i]}, {name}" for i, name in enumerate(names)
            ) + "?"
            card = choice("Who shall I call?", names, spoken=spoken)
            pending = _Pending(tuple(options[:3]), "choice")
        else:
            spoken = f"Did you mean {options[0].name}?"
            card = confirm(f"Call {options[0].name}?", spoken=spoken)
            pending = _Pending((options[0],), "confirm")
        card_id = self._cards.show(card)
        with self._lock:
            self._pending = {card_id: pending}  # only the card on screen counts
        return {
            "status": "unsure",
            "note": (
                f"She asked for '{said}' and it could be more than one person, so nothing is "
                f"dialled yet. Read this to her exactly and wait: \"{spoken}\""
            ),
        }

    def outcome(self, card_id: str) -> dict[str, Any] | None:
        with self._lock:
            contact = self._to_dial.pop(card_id, None)
            result = self._outcomes.pop(card_id, None)
        if contact is not None:
            return self._dial(contact)
        return result

    def _on_answer(self, answer: Answer) -> None:
        with self._lock:
            pending = self._pending.pop(answer.card_id, None)
        if pending is None:
            return
        chosen: Contact | None = None
        if pending.kind == "choice" and answer.choice is not None:
            chosen = pending.options[answer.choice - 1]  # 1-based, validated by the controller
        elif pending.kind == "confirm" and answer.yes:
            chosen = pending.options[0]
        if chosen is None:
            with self._lock:
                self._outcomes[answer.card_id] = {
                    "status": "not_calling",
                    "note": "Nothing was dialled. Ask her, briefly, who she'd like to call.",
                }
            return
        if answer.source == "voice":
            with self._lock:
                self._to_dial[answer.card_id] = chosen
            return
        threading.Thread(target=self._dial_logged, args=(chosen,), daemon=True).start()

    def _dial_logged(self, contact: Contact) -> None:
        result = self._dial(contact)
        if result.get("status") != "calling":
            logger.warning("tap-chosen call not placed: %s", result.get("status"))
