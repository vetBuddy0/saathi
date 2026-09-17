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
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import WSMsgType, web

from saathi.audio.capture import Capture
from saathi.core import Core, Event

_STATIC_DIR = Path(__file__).parent / "static"
_CAPTURE_CHUNK_BYTES = 3200  # 100ms of 16kHz mono 16-bit PCM

logger = logging.getLogger(__name__)


def _make_on_chunk(session):
    return lambda chunk: session.send_audio(chunk)


async def _run_turn(session, core: Core) -> None:
    """THINKING -> SPEAKING -> IDLE for one real turn. Runs the blocking
    STT/LLM/TTS work in an executor thread; every `core.handle()` call
    here still happens back on this event loop thread, in the `await`'s
    continuation, not inside the executor thread itself."""
    loop = asyncio.get_running_loop()
    try:
        reply_text = await loop.run_in_executor(None, session.end_turn)
    except Exception:
        logger.exception("turn failed")
        core.handle(Event("no_response"))
        return
    logger.info("reply: %s", reply_text)

    core.handle(Event("response_ready"))
    try:
        await loop.run_in_executor(None, session.say, reply_text)
    except Exception:
        logger.exception("speaking the reply failed")
    core.handle(Event("done"))


def build_app(core: Core, session=None, capture_source_id: str | None = None) -> web.Application:
    app = web.Application()
    websockets: set[web.WebSocketResponse] = set()
    live_capture: dict[str, Capture | None] = {"capture": None}

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
                    # press while SPEAKING has no transition (barge-in
                    # isn't built yet) and must not start a second capture
                    # on top of a turn that's still speaking. See core.py's
                    # module docstring for how this bug was found.
                    transitioned = core.handle(Event("press"))
                    if transitioned and session is not None and capture_source_id is not None:
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
                            asyncio.get_running_loop().create_task(_run_turn(session, core))
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
