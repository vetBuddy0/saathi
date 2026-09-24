"""`call_contact` — the real one. Replaces `stubs.py`'s handler and
nothing else, which was the whole point of the stub (SPEC.md, "Scope").

Registered here under the same name, same schema, same `"calls"`
permission as the stub, and only ever *called* through
`tools/registry.py`'s permission check from `cli.py` — this module
never calls the handler itself. "The voice engine never executes
anything. It emits intent; the core validates; the tool executes."

The result's `note` is what steers what she hears: the model speaks
*after* the tool returns (`cascade.py`'s `_continue_after_tool_call`),
so "Calling the test number." is a sentence in the note, never a
`session.say()` from in here — a tool speaking through `VoiceSession`
would be reaching through an interface (CLAUDE.md). The dial itself is
kicked off *by* the handler, because only the first tool call per turn
is handled (DECISIONS 2026-09-18): there is no second call to do it in.

Stage 1: only "the test number" (anything matching /test/i) is dialable,
and it rings `TWILIO_TEST_NUMBER`. Every other contact gets a polite
"not yet" note. Stages 2 and 3 (contacts in `IdentityStore`, name
matching) replace that branch.

Nothing in any result dict is a phone number or a SID: the dict is
serialized straight into the model's context and logged.
"""

from __future__ import annotations

import re
from typing import Any

from saathi.call.controller import CallController
from saathi.call.twilio import TwilioError
from saathi.tools.registry import Tool

CALL_CONTACT_DESCRIPTION = (
    "Place a phone call for her, when she asks to call someone (e.g. 'call the "
    "test number', 'ring my daughter'). Pass who she asked for as `contact`, in her "
    "words. Do not call this for talking *about* someone; only when she wants to "
    "phone them now."
)

_TEST_NUMBER_RE = re.compile(r"\btest\b", re.IGNORECASE)


def make_call_tool(controller: CallController) -> Tool:
    def _call_contact(contact: str) -> dict[str, Any]:
        if not _TEST_NUMBER_RE.search(contact or ""):
            return {
                "status": "unavailable",
                "note": (
                    "Calling people by name isn't set up yet; for now only the test "
                    "number can be called. Say so in one short, warm sentence -- no "
                    "apology, no 'anything else'."
                ),
            }
        if controller.active:
            return {
                "status": "busy",
                "note": "A call is already in progress. Say so in one short sentence.",
            }
        try:
            controller.dial_test_number()
        except TwilioError as exc:
            # str(exc) is sanitized by construction (call/twilio.py).
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
                "in a moment. Reply with exactly 'Calling the test number.' and nothing "
                "else -- she will hear it ring next."
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
