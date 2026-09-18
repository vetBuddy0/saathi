"""Serves `static/` and holds the WebSocket that connects `core.py` to the
Chromium kiosk.

Two message shapes cross this socket, and it interprets nothing beyond
them: `{"type": "state", "state": "<State value>"}` (server -> browser,
sent on connect and on every real transition) and
`{"type": "input", "event": "press" | "release"}` (browser -> server, one
per spacebar down/up). The browser decides nothing about what a press
*means* — that is `core.py`'s transition table — this module only carries
the event there and the resulting state back.

Checkpoint 1 had no voice engine, so `release` (`LISTENING` -> `THINKING`)
was immediately followed by a synthetic `no_response` event
(`THINKING` -> `IDLE`) rather than waiting for a real answer — SPEC.md's
"spacebar driving the state machine on fake events." That path is still
here, and still what runs with no `session` passed to `build_app` (every
checkpoint-1 test uses it): letting go of the spacebar with nothing to
answer should leave the face at IDLE, not stuck "thinking" forever.

One-hour spike (2026-09-17): when a `session` (`VoiceSession`) and
`capture_source_id` are supplied, `press` also starts real mic capture
and `release` stops it and runs the real turn — STT, reply, THINKING ->
SPEAKING -> IDLE with the state machine actually walking through
`SPEAKING` while the reply plays, not skipping past it. Runs in an
executor thread since Groq calls and Piper synthesis block; `core.handle()`
is still only ever called from this event loop thread, same invariant as
before, just reached via `run_in_executor`'s callback rather than
directly inline.

Barge-in (checkpoint 2): a `press` while `SPEAKING` is now a real
transition (`core.py`), and this module is what makes it *mean*
something — `session.interrupt()` stops the audio, and a fresh capture
starts, same as any other press. The turn still running in its executor
thread (blocked in `session.say()` a moment ago, now unblocked because
`interrupt()` just killed what it was waiting on) must not then fire
`done` on its way out — `core.py` already moved to `LISTENING` from the
press itself, and a stale `done` arriving after that would be exactly
the kind of "screen decides something core didn't" bug core.py's
docstring already tells the story of once. `_turn_generation` is how a
turn recognizes it's been superseded: every real turn start (a press
that begins listening, or a barge-in) bumps it, and a turn only acts on
its own tail end — `response_ready`, `done` — if the counter still
matches what it captured at the start.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from saathi.audio.capture import Capture
from saathi.core import Core, Event, State

_STATIC_DIR = Path(__file__).parent / "static"
_CAPTURE_CHUNK_BYTES = 3200  # 100ms of 16kHz mono 16-bit PCM

# Robustness pass (checkpoint 2): a 401/429/timeout/connection-reset from
# Groq used to be an uncaught exception with nowhere good to land — the
# turn's own try/except caught it and went straight to IDLE, silently.
# SPEC.md already has the answer for "thinking exceeds ~1.5s": say
# something short, out loud, rather than leave her looking at nothing.
# The same rule applies here. Never technical (no "401", no "rate
# limited") — she doesn't need the HTTP status, journalctl has it
# (logger.exception below keeps the real exception type and message).
_FALLBACK_REPLY_TEXT = "I didn't quite catch that. Let's try again in a moment."

logger = logging.getLogger(__name__)


def _make_on_chunk(session):
    return lambda chunk: session.send_audio(chunk)


async def _run_turn(session, core: Core, generation: int, turn_generation: dict) -> None:
    """THINKING -> SPEAKING -> IDLE for one real turn. Runs the blocking
    STT/LLM/TTS work in an executor thread; every `core.handle()` call
    here still happens back on this event loop thread, in the `await`'s
    continuation, not inside the executor thread itself.

    `generation` is this turn's stamp, taken from `turn_generation` when
    it started. If a barge-in has since bumped the counter, this turn has
    been superseded and must not touch state on its way out — the press
    that superseded it already did."""
    loop = asyncio.get_running_loop()
    try:
        reply_text = await loop.run_in_executor(None, session.end_turn)
    except Exception:
        logger.exception("turn failed (STT or LLM)")
        await _speak_and_finish(session, core, generation, turn_generation, _FALLBACK_REPLY_TEXT)
        return
    logger.info("reply: %s", reply_text)

    await _speak_and_finish(session, core, generation, turn_generation, reply_text)


async def _speak_and_finish(
    session, core: Core, generation: int, turn_generation: dict, text: str
) -> None:
    """THINKING/already-SPEAKING -> SPEAKING -> IDLE. Shared by the real
    reply and the fallback: both are "say this, then go back to IDLE",
    and a failure speaking the *fallback* (Piper is local — TTS itself
    doesn't depend on whatever just failed) still must not crash the
    turn or leave the state machine stuck."""
    loop = asyncio.get_running_loop()
    if turn_generation["value"] != generation:
        return
    core.handle(Event("response_ready"))
    try:
        await loop.run_in_executor(None, session.say, text)
    except Exception:
        logger.exception("speaking failed")
    if turn_generation["value"] != generation:
        return
    core.handle(Event("done"))


def build_app(core: Core, session=None, capture_source_id: str | None = None) -> web.Application:
    app = web.Application()
    websockets: set[web.WebSocketResponse] = set()
    live_capture: dict[str, Capture | None] = {"capture": None}
    turn_generation = {"value": 0}

    def broadcast_state(state, _event: Event) -> None:
        message = json.dumps({"type": "state", "state": state.value})
        for ws in list(websockets):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(message))

    core.subscribe(broadcast_state)

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "index.html")

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        websockets.add(ws)
        await ws.send_str(json.dumps({"type": "state", "state": core.state.value}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("dropped malformed message: %r", msg.data)
                    continue
                if payload.get("type") != "input":
                    continue
                kind = payload.get("event")
                if kind == "press":
                    # Only act if core.py actually transitioned — e.g. a
                    # press while IDLE/SLEEPING/SPEAKING with no session
                    # configured has no transition and must not start a
                    # capture. See core.py's module docstring for the bug
                    # that taught us this the first time.
                    was_speaking = core.state == State.SPEAKING
                    transitioned = core.handle(Event("press"))
                    if transitioned and session is not None and capture_source_id is not None:
                        if was_speaking:
                            # Barge-in: this press just superseded whatever
                            # turn was still speaking. Bump the generation
                            # *before* interrupting it, so its tail end
                            # (still unwinding in another thread) sees it's
                            # been superseded the moment it checks.
                            turn_generation["value"] += 1
                            session.interrupt()
                        session.start()
                        capture = Capture(
                            capture_source_id, _make_on_chunk(session), _CAPTURE_CHUNK_BYTES
                        )
                        capture.start()
                        live_capture["capture"] = capture
                elif kind == "release":
                    if core.handle(Event("release")):
                        if session is None:
                            core.handle(Event("no_response"))  # fake: no AI at checkpoint 1
                        else:
                            capture = live_capture.pop("capture", None)
                            if capture is not None:
                                capture.stop()
                            turn_generation["value"] += 1
                            generation = turn_generation["value"]
                            asyncio.get_running_loop().create_task(
                                _run_turn(session, core, generation, turn_generation)
                            )
        finally:
            websockets.discard(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static/", _STATIC_DIR)
    return app


def run(
    core: Core, host: str, port: int, session=None, capture_source_id: str | None = None
) -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(
        build_app(core, session=session, capture_source_id=capture_source_id),
        host=host,
        port=port,
        print=None,
    )
