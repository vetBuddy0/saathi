"""Saving a number by voice: gather what's missing across turns, read it
back digit by digit on a card, and write it only on a yes.

Exists because a wrong digit saved quietly is a call to a stranger,
repeatedly — so the write never happens in the turn she says the
number. The flow is:

1. `propose(name, number, relation)` merges what she just said into a
   *draft* (so she's asked only for what's actually missing — never the
   name again because the number was incomplete).
2. Anything missing -> a note asking for exactly that, nothing shown.
3. Complete -> an Entry card (`cards.entry`: the number on a dialpad,
   the name, both editable) is shown and its spoken text is returned as
   the tool's note. If the country code was inferred, that sentence
   leads: "That's a Singapore number, plus six five." Never silent.
   (Was a Read-back with Yes/No until 2026-09-26; the user asked for
   the dialpad so one wrong digit doesn't cost her the whole number.)
4. Her yes/no arrives later — a tap on the card, or the next turn's
   `answer_card` tool call — through `on_answer`. Yes carries whatever
   number and name the card shows by then; they are resolved again
   before writing (a tap can make a number too short, or drop a "+"
   so the country would be inferred unheard) and, if not a complete
   number, the card is shown again with the reason. Yes writes the
   contact (and learns the country); no keeps the name and relation
   and asks for the number again.

Never blocks: no `ask()`, no waiting inside a tool handler (see
`cards.py`). Pauses mid-number are handled twice: within one utterance
by the parser (commas, "then", "um"), and across turns by the draft — a
second fragment that doesn't restate the first is appended to it.

Contested: re-asking for the whole number after a "no" won over asking
"which digit?". Repairing one digit by voice means she has to hold the
old number in mind and name a position; saying it again is easier.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from saathi.call import contacts
from saathi.call.cards import Answer, CardController, entry, regroup_number
from saathi.call.numbers import SpokenNumber, parse_spoken_number, spell_digits
from saathi.call.phone import country_sentence, group_for_display, resolve

DRAFT_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class Draft:
    name: str | None = None
    relation: str | None = None
    digits: str = ""
    international: bool = False
    updated_at: float = 0.0


@dataclass(frozen=True)
class PendingSave:
    name: str
    relation: str | None
    e164: str
    country: str | None
    country_inferred: bool


def _merge_digits(
    draft: Draft, heard: SpokenNumber, locale: str | None
) -> tuple[str, bool]:
    """A fragment is appended only while the draft is *too short* — the
    one case where "the rest, after a pause" is the likely meaning. A
    draft that's already a complete number is replaced by new digits
    (she's giving a different number), as is one she restates from its
    start."""
    if not heard.digits:
        return draft.digits, draft.international
    if not draft.digits or heard.international:
        return heard.digits, heard.international
    if heard.digits.startswith(draft.digits):  # she restated it from the start
        return heard.digits, draft.international
    so_far = resolve(SpokenNumber(draft.digits, draft.international, 1.0), locale)
    if so_far.missing == ("more_digits",):
        return draft.digits + heard.digits, draft.international  # the rest, after a pause
    return heard.digits, False


class SaveFlow:
    def __init__(
        self,
        store_path: Path,
        cards: CardController,
        locale: Callable[[], str | None],
        on_country_confirmed: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(store_path)
        self._cards = cards
        self._locale = locale
        self._on_country_confirmed = on_country_confirmed
        self._clock = clock
        self._lock = threading.Lock()
        self._draft = Draft()
        self._pending: dict[str, PendingSave] = {}
        self._outcomes: dict[str, dict[str, Any]] = {}
        cards.on_answer(self._on_answer)

    @property
    def draft(self) -> Draft:
        return self._draft

    def pending_card_ids(self) -> set[str]:
        return set(self._pending)

    def propose(
        self, name: str | None = None, number: str | None = None, relation: str | None = None
    ) -> dict[str, Any]:
        heard = parse_spoken_number(number or "")
        locale = self._locale()
        with self._lock:
            draft = self._draft
            if self._clock() - draft.updated_at > DRAFT_TTL_SECONDS:
                draft = Draft()
            digits, international = _merge_digits(draft, heard, locale)
            draft = Draft(
                name=(name or "").strip() or draft.name,
                relation=contacts.normalise_relation(relation) or draft.relation,
                digits=digits,
                international=international,
                updated_at=self._clock(),
            )
            self._draft = draft

        missing: list[str] = []
        if not draft.name and not draft.relation:
            missing.append("who")
        resolved = resolve(SpokenNumber(draft.digits, draft.international, 1.0), locale)
        missing.extend(resolved.missing)
        if missing:
            return {"status": "need", "missing": missing, "note": _ask_for(missing, heard)}

        assert resolved.e164 is not None
        display_name = draft.name or draft.relation or ""
        pending = PendingSave(
            name=display_name,
            relation=draft.relation,
            e164=resolved.e164,
            country=resolved.country,
            country_inferred=resolved.country_inferred,
        )
        spoken = self._show(
            pending, group_for_display(resolved.e164), lead=country_sentence(resolved)
        )
        return {"status": "entry", "note": _read_note(spoken)}

    def _show(self, pending: PendingSave, shown: str, *, lead: str) -> str:
        """Show the Entry card for `pending` -- `shown` is the grouped
        number it displays -- and remember it as the one card that can
        be answered. Returns its spoken text."""
        spoken = " ".join(
            part
            for part in (
                lead,
                f"{_possessive(pending.name)} number is {_speak_shown(shown)}, "
                f"and the name, {pending.name}.",
                "Say yes to save, or change the number or the name.",
            )
            if part
        )
        card = entry(
            f"{_possessive(pending.name)} number", number=shown, name=pending.name, spoken=spoken
        )
        card_id = self._cards.show(card)
        with self._lock:
            self._pending = {card_id: pending}  # only the card on screen can be answered
        return spoken

    def outcome(self, card_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._outcomes.pop(card_id, None)

    def _on_answer(self, answer: Answer) -> None:
        with self._lock:
            pending = self._pending.pop(answer.card_id, None)
        if pending is None:
            return
        if answer.yes:
            edited = self._apply_edits(pending, answer)
            if edited is None:
                return  # shown again with the reason; nothing written
            pending = edited
            contacts.save_contact(
                self._path, pending.name, pending.e164, pending.country, pending.relation
            )
            if pending.country_inferred and pending.country and self._on_country_confirmed:
                self._on_country_confirmed(pending.country)
            with self._lock:
                self._draft = Draft()
                self._outcomes[answer.card_id] = {
                    "status": "saved",
                    "note": (
                        f"Saved. Tell her in a few words that {pending.name}'s number "
                        "is saved."
                    ),
                }
            return
        with self._lock:
            # Keep who it is; forget the digits. Ask for the number again.
            self._draft = replace(
                self._draft, digits="", international=False, updated_at=self._clock()
            )
            self._outcomes[answer.card_id] = {
                "status": "not_saved",
                "note": (
                    "Nothing was saved. Ask her, in one short sentence, to say the "
                    f"number for {pending.name} again."
                ),
            }


    def _apply_edits(self, pending: PendingSave, answer: Answer) -> PendingSave | None:
        """The yes carries the number and name as the card showed them.
        Unchanged: save as proposed. Changed: resolve the digits again
        (the country logic that checked the spoken number checks the
        tapped one), and if the result isn't a complete number -- or its
        country would now be inferred without ever having been said --
        show the card again with the reason and return None."""
        raw = str(answer.answer.get("number") or "")
        name = str(answer.answer.get("name") or "").strip() or pending.name
        if raw == pending.e164:
            return replace(pending, name=name)
        digits = raw.lstrip("+")
        resolved = resolve(SpokenNumber(digits, raw.startswith("+"), 1.0), self._locale())
        announced = pending.country if pending.country_inferred else None
        unheard = resolved.country_inferred and resolved.country != announced
        edited = PendingSave(
            name=name,
            relation=pending.relation,
            e164=resolved.e164 or pending.e164,
            country=resolved.country,
            country_inferred=resolved.country_inferred,
        )
        if resolved.e164 is not None and not unheard:
            return edited
        if resolved.e164 is None:
            # Her digits as tapped, in the grouping she was looking at.
            shown = regroup_number(str(answer.card.number or ""), raw)
            lead = _reason(resolved.missing)
        else:
            shown, lead = group_for_display(resolved.e164), country_sentence(resolved)
        spoken = self._show(edited, shown, lead=lead)
        with self._lock:
            self._outcomes[answer.card_id] = {
                "status": "check",
                "note": f"Nothing is saved yet. {_read_note(spoken)}",
            }
        return None


def _speak_shown(shown: str) -> str:
    """The number as the card shows it, digit by digit, a pause at each
    group; "plus" for a leading +."""
    parts = []
    for group in shown.split():
        if group.startswith("+"):
            parts.append(("plus " + spell_digits(group[1:])).strip())
        else:
            parts.append(spell_digits(group))
    return ", ".join(parts)


def _reason(missing: tuple[str, ...]) -> str:
    if "more_digits" in missing:
        return "That number is too short."
    if "check_number" in missing:
        return "That number has too many digits."
    if "country" in missing:
        return "I don't know which country that number is in."
    return "That isn't a whole number yet."


def _read_note(spoken: str) -> str:
    return (
        f'Read this to her exactly, then wait: "{spoken}" Do not say the number any '
        "other way. If she gives a different number or name, pass it to answer_card; "
        "her yes saves what the card shows."
    )


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _ask_for(missing: list[str], heard: SpokenNumber) -> str:
    asks = {
        "who": "whose number this is",
        "number": "the phone number",
        "country": "which country the number is in",
        "more_digits": "the rest of the number -- it's too short so far",
        "check_number": "the number again -- it has more digits than a phone number should",
    }
    wanted = " and ".join(asks[m] for m in missing if m in asks)
    extra = ""
    if heard.unknown:
        extra = f" You didn't catch these words: {', '.join(heard.unknown)}."
    return (
        f"Nothing is saved yet. Ask her only for {wanted}, in one short sentence -- "
        f"don't ask again for anything she has already said.{extra}"
    )
