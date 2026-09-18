"""`VoiceSession` — one of the five interfaces.

One-hour spike (2026-09-17): built just enough of this seam for one
concrete engine (`voice/engine/cascade.py`) to sit behind it, so closing
the loop today doesn't mean wiring Groq calls directly into
`screen/server.py`. `say()`, `on_audio()`, `on_intent()` and `interrupt()`
are declared because SPEC.md names them, but nothing calls them yet this
hour — no proactive speech, no streaming callbacks, no barge-in. Real stubs,
not decoration: the day those land, this Protocol is what they implement
against, not a redesign.

`end_turn()` is not in SPEC.md's original list. A batch STT engine needs
an explicit turn boundary — `send_audio()` alone never tells it "stop
buffering, transcribe now." Added here because the gap was blocking,
flagged rather than silently filled.
"""

from __future__ import annotations

from typing import Protocol


class VoiceSession(Protocol):
    def start(self) -> None:
        """Called once before the first turn."""
        ...

    def send_audio(self, chunk: bytes) -> None:
        """Feed one chunk of mic audio for the turn in progress."""
        ...

    def end_turn(self) -> str:
        """Everything fed since `start()`/the last `end_turn()` is one
        turn. Transcribes and replies, but does not speak — the caller
        fires its own SPEAKING transition and calls `say()` with the
        result, so the state machine visits SPEAKING for the actual
        audio, not just THINKING for the whole pipeline. Blocking,
        synchronous, for this hour only — a real implementation streams
        `on_audio`/`on_intent` instead of returning text to a caller."""
        ...

    def say(self, text: str) -> None:
        """Speak without a preceding turn — proactive speech. Not called
        this hour; initiative doesn't exist yet."""
        ...

    def on_audio(self, callback) -> None:
        """Register a callback for streamed reply audio. Not used this
        hour — `end_turn()` plays audio itself instead."""
        ...

    def on_intent(self, callback) -> None:
        """Register a callback for tool-call intents the engine emits.
        `callback(name: str, arguments: dict) -> Any` — called when the
        model asks to call a tool, with whatever `result` it returns fed
        straight back to the model as that tool's result. Implemented by
        `cascade.py` (item G): the engine only ever calls `callback`; it
        never validates a permission or runs a handler itself — "the
        voice engine never executes anything. It emits intent; the core
        validates; the tool executes" (SPEC.md). Which tools the model
        can even ask for is a *separate* concern
        (`CascadeSession`'s `tool_schemas` constructor argument) from
        registering this callback — the two only work together."""
        ...

    def interrupt(self) -> None:
        """Stop speaking immediately — barge-in's hook. Not called this
        hour; barge-in is explicitly deferred (see `core.py`)."""
        ...
