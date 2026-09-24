"""`call_contact`, `save_contact`, `answer_card` — calling's tools.

`call_contact` replaces `stubs.py`'s handler and nothing else, which was
the whole point of the stub (SPEC.md, "Scope"): same name, same schema,
same `"calls"` permission. All three are only ever *called* through
`tools/registry.py`'s permission check from `cli.py` — this module never
calls a handler itself. "The voice engine never executes anything. It
emits intent; the core validates; the tool executes."

The result's `note` is what steers what she hears: the model speaks
*after* the tool returns (`cascade.py`'s `_continue_after_tool_call`),
so "Calling Priya." is a sentence in the note, never a `session.say()`
from in here — a tool speaking through `VoiceSession` would be reaching
through an interface (CLAUDE.md). The dial is kicked off *by* the
handler, because only the first tool call per turn is handled
(DECISIONS 2026-09-18): there is no second call to do it in. Ringing
takes seconds; the sentence is spoken before it rings.

Stage 2 resolution for `call_contact`, in order: "the test number"; a
relationship word ("my daughter") through her `edges`; an exact
(accent- and case-insensitive) name. Anything else is *no match*: say
so and offer to save a number — never dial a guess. (Stage 3 adds
fuzzy name matching between "exact" and "no match".)

`answer_card` is how a *spoken* answer reaches a card calling showed:
her "yes" arrives as the next turn's tool call and becomes
`cards.answer(id, ..., source="voice")`. If SCREEN ships its own
voice-answer tool, this one is dropped in favour of it at merge (see
`docs/completed/calling.md`).

Permissions: `save_contact` writes her memory, so it is `"contacts"`,
not `"calls"` — saving a number has no external consequence; placing a
call does. `answer_card` inherits `"calls"`: it can complete a save or,
in Stage 3, pick who to ring.

Nothing in any result dict is a phone number or a SID: results are
serialised into the model's context and logged. The read-back note is
the one exception by design — it carries the digits *as words*, because
reading them to her is the point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from saathi.call import contacts
from saathi.call.cards import CardController
from saathi.call.controller import CallController
from saathi.call.saving import SaveFlow
from saathi.call.twilio import TwilioError
from saathi.tools.registry import Tool

CALL_CONTACT_DESCRIPTION = (
    "Place a phone call for her, when she asks to call someone (e.g. 'call my "
    "daughter', 'ring Priya', 'call the test number'). Pass who she asked for as "
    "`contact`, in her words. Do not call this for talking *about* someone; only when "
    "she wants to phone them now."
)
SAVE_CONTACT_DESCRIPTION = (
    "Save someone's phone number when she asks (e.g. 'save my daughter Priya's number, "
    "nine one two three...'). Pass `number` exactly as she said it, words and all -- "
    "never convert or correct digits yourself. Pass `name` and/or `relation` if she said "
    "them. Call it again with just the new part if she adds more (e.g. the rest of the "
    "number) after being asked."
)
ANSWER_CARD_DESCRIPTION = (
    "Her spoken answer to the card on screen that you just read to her: `yes` true/false "
    "for a read-back ('yes, that's right' / 'no'), or `choice` 1, 2 or 3 for a choice "
    "('the first one' is 1)."
)

_TEST_NUMBER_RE = re.compile(r"\btest\b", re.IGNORECASE)


class _CardFlow(Protocol):
    def pending_card_ids(self) -> set[str]: ...

    def outcome(self, card_id: str) -> dict[str, Any] | None: ...


def _dial(controller: CallController, number: str, who: str) -> dict[str, Any]:
    if controller.active:
        return {
            "status": "busy",
            "note": "A call is already in progress. Say so in one short sentence.",
        }
    try:
        controller.dial(number)
    except TwilioError as exc:
        return {
            "status": "error",
            "detail": str(exc),  # sanitized by construction (call/twilio.py)
            "note": (
                "The call could not be placed just now. Say so plainly in one short "
                "sentence and suggest trying again in a moment."
            ),
        }
    return {
        "status": "calling",
        "note": (
            f"The call to {who} is being placed right now and will ring in a moment. "
            f"Reply with exactly 'Calling {who}.' and nothing else."
        ),
    }


def make_call_tool(controller: CallController, store_path: Path | None = None) -> Tool:
    """`store_path=None` is Stage 1 behaviour: only the test number."""

    def _call_contact(contact: str) -> dict[str, Any]:
        contact = (contact or "").strip()
        if _TEST_NUMBER_RE.search(contact):
            if controller.active:
                return _dial(controller, "", "the test number")
            try:
                controller.dial_test_number()
            except TwilioError as exc:
                return {
                    "status": "error",
                    "detail": str(exc),
                    "note": (
                        "The call could not be placed just now. Say so plainly in one "
                        "short sentence and suggest trying again in a moment."
                    ),
                }
            return {
                "status": "calling",
                "note": (
                    "The call to the test number is being placed right now and will ring "
                    "in a moment. Reply with exactly 'Calling the test number.' and "
                    "nothing else."
                ),
            }
        if store_path is None:
            return {
                "status": "unavailable",
                "note": (
                    "Calling people by name isn't set up yet; for now only the test "
                    "number can be called. Say so in one short, warm sentence."
                ),
            }
        match = None
        if contacts.is_known_relation(contact):
            match = contacts.find_by_relation(store_path, contact)
        if match is None:
            match = contacts.find_by_exact_name(store_path, contact)
        if match is None:
            return {
                "status": "no_match",
                "note": (
                    f"There is no number saved for '{contact}'. Say so plainly in one "
                    "sentence and offer to save their number. Do not guess who she meant."
                ),
            }
        return _dial(controller, match.phone, match.name)

    return Tool(
        name="call_contact",
        schema={
            "type": "object",
            "properties": {"contact": {"type": "string"}},
            "required": ["contact"],
        },
        permission="calls",
        handler=_call_contact,
    )


def make_save_contact_tool(flow: SaveFlow) -> Tool:
    def _save_contact(
        number: str | None = None, name: str | None = None, relation: str | None = None
    ) -> dict[str, Any]:
        return flow.propose(name=name, number=number, relation=relation)

    return Tool(
        name="save_contact",
        schema={
            "type": "object",
            "properties": {
                "number": {"type": "string"},
                "name": {"type": "string"},
                "relation": {"type": "string"},
            },
        },
        permission="contacts",
        handler=_save_contact,
    )


def make_answer_card_tool(cards: CardController, flows: list[_CardFlow]) -> Tool:
    def _answer_card(yes: bool | None = None, choice: int | None = None) -> dict[str, Any]:
        for flow in flows:
            for card_id in flow.pending_card_ids():
                payload: dict[str, Any] = {}
                if yes is not None:
                    payload["yes"] = bool(yes)
                if choice is not None:
                    payload["choice"] = int(choice)  # "the first one" is 1, same as the card
                if not payload:
                    continue
                if cards.answer(card_id, payload, source="voice"):
                    result = flow.outcome(card_id)
                    if result is not None:
                        return result
                    return {"status": "answered", "note": "Acknowledge in a few words."}
        return {
            "status": "no_card",
            "note": "There is nothing waiting for an answer. Carry on the conversation.",
        }

    return Tool(
        name="answer_card",
        schema={
            "type": "object",
            "properties": {
                "yes": {"type": "boolean"},
                "choice": {"type": "integer", "minimum": 1, "maximum": 3},
            },
        },
        permission="calls",
        handler=_answer_card,
    )
