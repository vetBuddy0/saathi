"""`set_language` — item G's spoken entry point: "speak to me in
Mandarin" must write the same preference through the same path as the
Ctrl+L panel (`identity/preferences.py`'s `write_preference`), not a
second, parallel way of doing the same thing.

The first real (non-stub) tool this project registers. Goes through
`tools/registry.py`'s permission check exactly like `call_contact`/
`play_music` do — SPEC.md: "The voice engine never executes anything. It
emits intent; the core validates; the tool executes." This module only
builds the `Tool`; nothing here calls the handler directly. See
`voice/engine/cascade.py`'s `on_intent` wiring for where the engine
*emits* the intent, and `cli.py` for where it's *validated* (the
permission check) and *executed* (the handler, via `Registry.call()`).

`SUPPORTED_LANGUAGES` (`voice/language.py`) is read directly for the
schema's enum, never copied — same rule the Ctrl+L panel follows,
enforced here so the LLM is never even offered a language nothing can
actually speak.
"""

from __future__ import annotations

from typing import Any

from saathi.identity.preferences import LANGUAGE_KEY, PreferenceLocked, write_preference
from saathi.identity.store import IdentityStore
from saathi.tools.registry import Tool
from saathi.voice.language import SUPPORTED_LANGUAGES

SET_LANGUAGE_DESCRIPTION = (
    "Switch the language you reply in from now on, when she explicitly asks "
    "for a different language (e.g. 'speak to me in Mandarin'). Do not call "
    "this just because she said a few words in another language -- only "
    "when she's asking for the switch itself."
)


def make_set_language_tool(store: IdentityStore) -> Tool:
    def _set_language(language: str) -> dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            # The schema's enum should already keep this from happening --
            # a model calling with an out-of-enum value anyway must not
            # silently succeed or corrupt the stored preference.
            return {
                "status": "unsupported",
                "language": language,
                "supported": sorted(SUPPORTED_LANGUAGES),
            }
        try:
            write_preference(store, LANGUAGE_KEY, language)
        except PreferenceLocked as exc:
            # Real, current limitation -- see identity/preferences.py's
            # docstring. Reported back to the model as a normal tool
            # result, not an exception that takes the turn down, so it
            # can apologize honestly instead of the reply just vanishing.
            return {"status": "locked", "language": language, "reason": str(exc)}
        return {
            "status": "ok",
            "language": language,
            # Found by a real end-to-end run, not guessed: without this,
            # the model doesn't know the switch already succeeded and
            # phrases a confusing reply ("I'll continue in English as
            # instructed") that sounds like a refusal for a request it
            # actually just granted. This turn's own reply is still in
            # whatever language was current *before* the call (see
            # cascade.py's end_turn() -- the switch is "effective next
            # turn" by design), so the model needs to be told that
            # explicitly to say something coherent about it now.
            "note": (
                f"The switch to {language} succeeded and will take effect starting with "
                "her next turn -- this reply should still finish in whatever language "
                "you were already asked to reply in for this turn, and should confirm "
                "the switch positively, not sound like a refusal."
            ),
        }

    return Tool(
        name="set_language",
        schema={
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "enum": sorted(SUPPORTED_LANGUAGES),
                }
            },
            "required": ["language"],
        },
        permission="preferences",
        handler=_set_language,
    )
