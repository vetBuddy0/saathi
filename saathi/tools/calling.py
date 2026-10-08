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

Resolution for `call_contact`, in order: "the test number"; a
relationship word ("my daughter") through her `edges`; then her saved
names through `call/match.py`, which answers in three bands —
confident (say "Calling Basudeb.", then dial), unsure (a Choice or
Confirm card via `call/choosing.py`; nothing dials until she answers),
none (say so, offer to save a number; never dial a guess). An exact
name goes through the matcher too, not around it: it scores 1.0, and is
confident unless a sound-alike is also saved — which is exactly when
Whisper's spelling of the day must not decide who rings.

`answer_card` is how a *spoken* answer reaches a card calling showed:
her "yes" arrives as the next turn's tool call and becomes
`cards.answer(id, ..., source="voice")`. If SCREEN ships its own
voice-answer tool, this one is dropped in favour of it at merge (see
`docs/completed/calling.md`).

Permissions: `save_contact` writes her memory, so it is `"contacts"`,
not `"calls"` — saving a number has no external consequence; placing a
call does. `answer_card` inherits `"calls"`: it can complete a save or,
in Stage 3, pick who to ring.

Routing (2026-10-07): anyone paired with the free family app is rung
through it (`call/routing.py`, `call/webrtc.py`); the phone network is
the fallback, only when Twilio is configured and the person isn't
paired. Paired members with no saved number are matchable by name and
relation like any contact. The resolution order above is unchanged.

Nothing in any result dict is a phone number or a SID: results are
serialised into the model's context and logged. The read-back note is
the one exception by design — it carries the digits *as words*, because
reading them to her is the point.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Protocol

from saathi.call import contacts
from saathi.call.cards import CardController
from saathi.call.choosing import ChoiceFlow
from saathi.call.match import match as match_name
from saathi.call.match import similarity
from saathi.call.controller import CallController
from saathi.call.saving import SaveFlow
from saathi.call.relay import RelayError
from saathi.call.routing import FamilyRoute, no_phone_route
from saathi.call.twilio import TwilioError
from saathi.tools.registry import Tool

logger = logging.getLogger(__name__)

CALL_CONTACT_DESCRIPTION = (
    "Place a phone call for her, when she asks to call someone (e.g. 'call my "
    "daughter', 'ring Priya', 'call the test number'). Pass who she asked for as "
    "`contact`, in her words ('my son', not 'your son'). ALWAYS call this tool for any "
    "request to phone someone -- never answer from memory that a number isn't saved; "
    "only this tool knows her contacts, and it will show her the saved ones if it "
    "isn't sure. Do not call this for talking *about* someone; only when she wants to "
    "phone them now."
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
        controller.dial(number, who=who)
    except (TwilioError, RelayError) as exc:
        # RelayError too (TODO M5): a tunnel that died is a plain "not
        # just now", never a turn lost to an untranslated exception.
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


def route_call(
    controller: CallController | None,
    family: FamilyRoute | None,
    contact: contacts.Contact,
    phone_ready: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """The family app if `contact` is paired, else the phone network if
    it is configured and ready (DECISIONS 2026-10-07, `call/routing.py`).
    `phone_ready()` is None when Twilio can dial, else why not."""
    if family is not None:
        result = family.dial(contact.name)
        if result is not None:
            return result
    if controller is None or not contact.phone:
        return no_phone_route(contact.name)
    if family is not None and family.active:
        return {
            "status": "busy",
            "note": "A call is already in progress. Say so in one short sentence.",
        }
    reason = phone_ready() if phone_ready is not None else None
    if reason:
        return {
            "status": "unavailable",
            "note": f"{reason} Say so plainly in one short sentence; don't offer to try.",
        }
    return _dial(controller, contact.phone, contact.name)


def make_contact_dialer(
    controller: CallController | None,
    family: FamilyRoute | None = None,
    phone_ready: Callable[[], str | None] | None = None,
):
    """What `ChoiceFlow` calls once she has picked someone — the same
    `route_call` path as a confident match, so the note and the
    busy/error handling can't drift apart."""

    def dial(contact: contacts.Contact) -> dict[str, Any]:
        return route_call(controller, family, contact, phone_ready)

    return dial


def make_call_tool(
    controller: CallController | None,
    store_path: Path | None = None,
    choices: ChoiceFlow | None = None,
    family: FamilyRoute | None = None,
    phone_ready: Callable[[], str | None] | None = None,
) -> Tool:
    """`store_path=None` is Stage 1 behaviour: only the test number.
    Without `choices`, an unsure match is answered as no match — never
    dialled. `controller=None`: no phone network (Twilio isn't set up),
    so only paired family members can be rung. `family`: the free
    family app, tried first for anyone paired (`call/routing.py`)."""

    def _route(contact: contacts.Contact) -> dict[str, Any]:
        return route_call(controller, family, contact, phone_ready)

    def _candidates() -> list[contacts.Contact]:
        saved = contacts.list_contacts(store_path)
        return family.contacts(saved) if family is not None else saved

    def _call_contact(contact: str) -> dict[str, Any]:
        contact = (contact or "").strip()
        # Her words, never a number: what a mis-dial is debugged from.
        logger.info("call_contact asked for %r", contact)
        if _TEST_NUMBER_RE.search(contact):
            if controller is None:
                return no_phone_route("The test number")
            reason = phone_ready() if phone_ready is not None else None
            if reason:
                return {
                    "status": "unavailable",
                    "note": f"{reason} Say so plainly in one short sentence; don't offer to try.",
                }
            if controller.active:
                return _dial(controller, "", "the test number")
            try:
                controller.dial_test_number()
            except (TwilioError, RelayError) as exc:
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
        candidates = _candidates()
        if contacts.is_known_relation(contact):
            related = contacts.find_by_relation(store_path, contact)
            if related is None and family is not None:
                wanted = contacts.normalise_relation(contact)
                related = next((c for c in candidates if wanted in c.relations), None)
            if related is not None:
                return _route(related)
        result = match_name(contact, candidates, lambda c: c.name)
        if result.band == "confident":
            return _route(result.best)
        if result.band == "unsure" and choices is not None:
            return choices.offer(contact, result.choices())
        # Nothing matched, but she has saved contacts: ask from them rather
        # than saying "no number" -- a relation the parser didn't know, or a
        # name Whisper spelled oddly, is far likelier than a stranger. Up to
        # three on a card (one -> "Did you mean …?"); still never dials
        # without her answer. The user asked for this live, 2026-09-25.
        # Closest-sounding first, not first-saved: a fourth contact was
        # otherwise never on the card (live, 2026-10-05). Ties keep
        # saved order.
        saved = sorted(
            candidates,
            key=lambda c: similarity(contact, c.name),
            reverse=True,
        )
        if saved and choices is not None:
            return choices.offer(contact, saved[:3])
        return {
            "status": "no_match",
            "note": (
                f"There is no number saved for '{contact}'. Say so plainly in one "
                "sentence and offer to save their number. Do not guess who she meant."
            ),
        }

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
