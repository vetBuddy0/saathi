"""Calling's view of the screen cards -- now the real ones.

Why this module still exists: Calling was built before PR #3's
`saathi/screen/cards.py` had landed, against a stub of the same shape
(choices 1-based, `on_answer` callbacks synchronous on the answering
thread -- both checked against PR #3 before merge). At reconciliation
the stub was replaced by this re-export rather than by rewriting every
import in `saathi/call/`: one line of indirection, and there is exactly
one card implementation in the codebase. The option that lost was
keeping the stub alongside the real module "for tests" -- two
implementations of the same contract drift, and Calling's tests would
have kept passing against the copy while the screen ran the other.

`FakeCardController` is the REAL controller with no broadcast attached
(nothing on screen), plus a record of what was shown, so Calling's
tests exercise the real validation, one-at-a-time and callback rules.
"""

from __future__ import annotations

from saathi.screen.cards import (  # noqa: F401  (re-exported)
    Answer,
    Card,
    CardController,
    TooManyOptions,
    choice,
    confirm,
    readback,
    validate_answer,
)


class FakeCardController(CardController):
    def __init__(self) -> None:
        super().__init__(broadcast=None)
        self.shown: list[Card] = []

    def show(self, card: Card) -> str:
        self.shown.append(card)
        return super().show(card)
