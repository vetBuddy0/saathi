"""Serves `static/` and holds the WebSocket that connects `core.py` to the
Chromium kiosk.

Four message shapes cross this socket now (checkpoint 2 grew it from
two): `{"type": "state", ...}` and `{"type": "input", ...}` as before,
plus `{"type": "settings", ...}` (server -> browser, sent on connect and
after every successful preference write — languages, TTS backends, and
which one is currently preferred) and
`{"type": "set_preference", "key": ..., "value": ...}` (browser ->
server, from the Ctrl+L panel — item C/G). The browser still decides
nothing about what a press *means*; the settings panel is explicitly the
one exception to "the browser only carries events" — item C/G's brief
asks for it to be a real, if thin, settings surface (language + TTS
backend), not just another passthrough, because it's for whoever sets
the device up, not for her — see `static/js/settings-panel.js`.

The YouTube stream (2026-09-25) added two more: `{"type": "media",
"action": ...}` (server -> browser: results, play, pause, resume, stop,
volume, layout — emitted by `tools/media.py`'s controller through the
`broadcast` seam `build_app` installs on it, never on connect) and
`{"type": "media_event", "event": ...}` (browser -> server: the player
reporting `ended`/`error`). Ducking needs no new message: the browser
lowers the player's volume on the `state` it already receives
(`listening`/`thinking`/`speaking`) — see `static/js/media-policy.js`.

Cards (same day): `{"type": "card", "card": {...} | null}` (server ->
browser: show this one card, or clear it; emitted by `screen/cards.py`'s
CardController through the same seam, never on connect) and
`{"type": "card_answer", "id": ..., "answer": {...}}` (browser -> server:
a tap). A voice answer never crosses this socket: it arrives as a tool
call and the tool calls the same `CardController.answer()`. The hold
seam (`build_app(hold=)`) is the one place this server does something
with a press other than hand it to `core.py`: while a `HoldController`
has a handler, press/release drive a hold timer here instead — see the
seam's comment in `build_app`.

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
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import WSMsgType, web

from saathi.audio.capture import Capture
from saathi.core import Core, Event, State
from saathi.identity.preferences import (
    LANGUAGE_KEY,
    TTS_BACKEND_KEY,
    read_preference,
    write_preference,
)
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends

_STATIC_DIR = Path(__file__).parent / "static"
_CAPTURE_CHUNK_BYTES = 3200  # 100ms of 16kHz mono 16-bit PCM
# Holding-card progress cadence: 20 updates over a 2 s hold is smooth
# enough to read as "it's doing something" without flooding the socket.
_HOLD_TICK_SECONDS = 0.1

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


def _log_turn(store, session, eou_ms: int | None) -> None:
    """Item E: one row per turn that actually completed (superseded/
    interrupted turns never reach here — see `_speak_and_finish` below).
    `turns`'s per-stage columns (2026-09-18 schema) are only as good as
    `session.pop_last_turn_timings()` — absent for a fake session in a
    test, or for a real one that never got past `end_turn()` some other
    way (e.g. `smoke.py`'s barge-in check calls `say()` directly and
    correctly produces no timings — see cascade.py). A logging failure
    must never take the turn down with it, hence the broad except."""
    if store is None:
        return
    pop_timings = getattr(session, "pop_last_turn_timings", None)
    timings = pop_timings() if pop_timings is not None else None
    try:
        store.append(
            "turns",
            ts=datetime.now(timezone.utc).isoformat(),
            mode="voice",  # the only mode this device has today (SPEC.md names no others)
            eou_ms=eou_ms,
            stt_ms=timings.stt_ms if timings else None,
            first_token_ms=timings.first_token_ms if timings else None,
            first_tts_chunk_ms=timings.first_tts_chunk_ms if timings else None,
            prompt_tokens=timings.prompt_tokens if timings else None,
            completion_tokens=timings.completion_tokens if timings else None,
            cost_usd=timings.cost_usd if timings else None,
            handoff=0,
            engine="cascade",
        )
    except Exception:
        logger.exception("failed to log turn")


async def _run_turn(
    session, core: Core, generation: int, turn_generation: dict, store, eou_ms: int | None
) -> None:
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
        await _speak_and_finish(
            session, core, generation, turn_generation, _FALLBACK_REPLY_TEXT, store, eou_ms
        )
        return
    if not reply_text.strip():
        # Nothing was said (the session's speech gate, or an empty
        # transcript -- see cascade.py's end_turn()). THINKING -> IDLE
        # via no_response, never SPEAKING: no fallback line, no "I
        # didn't catch that". And no `turns` row: a turn is an exchange,
        # and this wasn't one -- logging it would put a row with no
        # STT, LLM or TTS time into the p95 the latency budget reads.
        if turn_generation["value"] != generation:
            return
        core.handle(Event("no_response"))
        logger.info("nothing said; turn ended silently")
        return
    logger.info("reply: %s", reply_text)

    await _speak_and_finish(session, core, generation, turn_generation, reply_text, store, eou_ms)


async def _speak_and_finish(
    session,
    core: Core,
    generation: int,
    turn_generation: dict,
    text: str,
    store=None,
    eou_ms: int | None = None,
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
    _log_turn(store, session, eou_ms)


def _settings_message(store) -> str:
    # Rebuilt fresh on every call, not cached: a backend's available()
    # (GCP credentials dropped in after boot, kokoro installed later)
    # can change while the process runs, and the panel is opened rarely
    # enough that this cost is a non-issue — see voice/tts/__init__.py's
    # docstring for the same "checked lazily" principle.
    backends = []
    for backend in default_backends().values():
        available, reason = backend.available()
        backends.append(
            {
                "id": backend.id,
                "display_name": backend.display_name,
                "local": backend.local,
                "available": available,
                "reason": reason,
                "cost_per_million_chars_usd": backend.cost_per_million_chars_usd(),
            }
        )
    current_language = DEFAULT_LANGUAGE
    current_backend = DEFAULT_BACKEND_ID
    if store is not None:
        current_language = read_preference(store, LANGUAGE_KEY, DEFAULT_LANGUAGE)
        current_backend = read_preference(store, TTS_BACKEND_KEY, DEFAULT_BACKEND_ID)
    return json.dumps(
        {
            "type": "settings",
            "languages": list(SUPPORTED_LANGUAGES),
            "current_language": current_language,
            "backends": backends,
            "current_backend": current_backend,
        }
    )


def build_app(
    core: Core,
    session=None,
    capture_source_id: str | None = None,
    store=None,
    media=None,
    cards=None,
    hold=None,
) -> web.Application:
    app = web.Application()
    websockets: set[web.WebSocketResponse] = set()
    live_capture: dict[str, Capture | None] = {"capture": None}
    turn_generation = {"value": 0}
    turn_started_at: dict[str, float | None] = {"value": None}
    hold_task: dict[str, asyncio.Task | None] = {"task": None}

    def _send_all(message: str) -> None:
        for ws in list(websockets):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(message))

    def broadcast_state(state, _event: Event) -> None:
        _send_all(json.dumps({"type": "state", "state": state.value}))

    core.subscribe(broadcast_state)

    # The broadcast seam. `media` (tools/media.py's MediaController) and
    # `cards` (screen/cards.py's CardController) are anything with
    # `set_broadcast(fn)`. Their callers run on the executor thread
    # inside `session.end_turn()`, where touching `websockets` is
    # unsafe, so the callable they're given hops to this loop first.
    # The server still decides nothing: the tool says what the screen
    # shows, this only carries it. Installed on startup, not here,
    # because there is no running loop at build time; a message before
    # then has no screen to reach anyway and is dropped.
    loop_holder: dict[str, asyncio.AbstractEventLoop | None] = {"loop": None}

    def broadcast_threadsafe(payload: dict) -> None:
        loop = loop_holder["loop"]
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(_send_all, json.dumps(payload))

    seams = [obj for obj in (media, cards) if obj is not None]
    if seams:

        async def install_seams(_app: web.Application) -> None:
            loop_holder["loop"] = asyncio.get_running_loop()
            for obj in seams:
                obj.set_broadcast(broadcast_threadsafe)

        async def remove_seams(_app: web.Application) -> None:
            for obj in seams:
                obj.set_broadcast(None)
            loop_holder["loop"] = None

        app.on_startup.append(install_seams)
        app.on_cleanup.append(remove_seams)

    # The hold seam (screen/cards.py's HoldController). While a handler
    # is set, the spacebar is a hold-to-confirm button and core.py never
    # hears the press: a short press does nothing, a press held for
    # `hold.seconds` fires the handler once. The timer is here because
    # the loop is here; the controller only knows how far along the
    # hold is. The browser sends exactly one press and one release per
    # hold (main.js ignores key repeat), so progress is measured from
    # the press timestamp, never from repeated events.
    async def run_hold(started_at: float) -> None:
        try:
            while True:
                await asyncio.sleep(_HOLD_TICK_SECONDS)
                if hold.tick(time.monotonic() - started_at):
                    return
        finally:
            hold_task["task"] = None

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "index.html")

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        websockets.add(ws)
        await ws.send_str(json.dumps({"type": "state", "state": core.state.value}))
        await ws.send_str(_settings_message(store))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("dropped malformed message: %r", msg.data)
                    continue
                if payload.get("type") == "set_preference":
                    key = payload.get("key")
                    value = payload.get("value")
                    if key not in (LANGUAGE_KEY, TTS_BACKEND_KEY) or not isinstance(value, str):
                        logger.warning("dropped malformed set_preference: %r", payload)
                        continue
                    if store is None:
                        await ws.send_str(
                            json.dumps(
                                {
                                    "type": "preference_result",
                                    "key": key,
                                    "ok": False,
                                    "reason": "no identity store configured on this run",
                                }
                            )
                        )
                        continue
                    write_preference(store, key, value)
                    await ws.send_str(
                        json.dumps(
                            {"type": "preference_result", "key": key, "ok": True, "reason": ""}
                        )
                    )
                    _send_all(_settings_message(store))
                    continue
                if payload.get("type") == "media_event":
                    # The player reporting back ("ended", "error"). A
                    # report, not a decision: the controller updates
                    # what "play that again" means; nothing here does.
                    event = payload.get("event")
                    video_id = payload.get("video_id")
                    if media is not None and isinstance(event, str):
                        media.on_browser_event(
                            event, video_id if isinstance(video_id, str) else None
                        )
                    continue
                if payload.get("type") == "card_answer":
                    # A tap. The same entry point a voice answer uses
                    # (CardController.answer); a stale id or a malformed
                    # answer is dropped there, not here.
                    if cards is not None:
                        card_id = payload.get("id")
                        if isinstance(card_id, str):
                            cards.answer(card_id, payload.get("answer"), source="tap")
                    continue
                if payload.get("type") != "input":
                    continue
                kind = payload.get("event")
                if hold is not None and hold.active:
                    # The button means "hold to confirm" for now; core.py
                    # never sees this press. See the hold seam above.
                    if kind == "press" and hold_task["task"] is None:
                        hold.begin()
                        hold_task["task"] = asyncio.get_running_loop().create_task(
                            run_hold(time.monotonic())
                        )
                    elif kind == "release":
                        task = hold_task["task"]
                        if task is not None:
                            task.cancel()
                            hold_task["task"] = None
                        hold.abandon()
                    continue
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
                        turn_started_at["value"] = time.monotonic()
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
                            eou_ms = None
                            started_at = turn_started_at["value"]
                            if started_at is not None:
                                eou_ms = round((time.monotonic() - started_at) * 1000)
                            turn_generation["value"] += 1
                            generation = turn_generation["value"]
                            asyncio.get_running_loop().create_task(
                                _run_turn(session, core, generation, turn_generation, store, eou_ms)
                            )
        finally:
            websockets.discard(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static/", _STATIC_DIR)
    return app


def run(
    core: Core,
    host: str,
    port: int,
    session=None,
    capture_source_id: str | None = None,
    store=None,
    media=None,
    cards=None,
    hold=None,
) -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(
        build_app(
            core,
            session=session,
            capture_source_id=capture_source_id,
            store=store,
            media=media,
            cards=cards,
            hold=hold,
        ),
        host=host,
        port=port,
        print=None,
    )
