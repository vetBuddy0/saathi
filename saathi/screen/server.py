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
(`attentive`/`listening`/`thinking`/`speaking`) — see
`static/js/media-policy.js`.

Cards (same day): `{"type": "card", "card": {...} | null}` (server ->
browser: show this one card, or clear it; emitted by `screen/cards.py`'s
CardController through the same seam, and re-sent to a fresh connection
while one is up -- see the last paragraph) and
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

A tap answering a card ends the exchange (2026-09-26). Found in a real
headless Chromium against this server: a search turn shows the card
while still THINKING, then spends the rest of the turn reading the
three titles out; a tap on option two mid-reading cleared the card and
started the video, and the reading carried on regardless — "she keeps
talking instead of just playing the thing". The card answered *is* the
turn's answer, so an accepted tap on a card shown by the live turn
either interrupts the reply (`SPEAKING`, the turn's own tail then fires
`done` as usual) or, if the reply hasn't started (`THINKING`),
supersedes the turn and closes it with `no_response` — nothing left to
say. A card shown by an earlier turn, or between turns, ends nothing.
The same run showed why a card is re-sent on connect: the browser is
the only copy of it, and a reload left a question pending on the
server with nothing on the screen to answer.

Emotions and the phone panel (2026-10-07) ride the same broadcast
seam. `{"type": "emotion", "emotion": ..., "seconds": ...}` (server ->
browser) comes from `screen/emotion.py`'s EmotionController -- the one
door for "blush now"; the browser draws it and lets it lapse. `{"type":
"call", "call": {...} | null}` comes from `screen/call_panel.py`'s
CallPanel, fed by the call controller, and is re-sent on connect like a
card. `{"type": "call_hangup", "id": ...}` (browser -> server) is the
panel's End call button: checked against the panel's current call id,
then handed to the hold seam's `complete()` -- the very handler a
two-second spacebar hold fires, never a direct hang-up from here.

The open conversation (2026-10-08, `follow_up_seconds`): after a spoken
reply the turn ends in ATTENTIVE, not IDLE, with a hands-free
`ListenWindow` on the mic -- a follow-up needs no name (SPEC.md,
"Triggers"). A quiet window closes the conversation. Not after a reply
that changed what is playing (the song should be heard, not held ducked),
nor during a call. A turn that was only her name goes ATTENTIVE the same
way and, if she stays quiet, Saathi says a short "Yes?" (the session's
`name_prompt`). Ducking needs nothing new for any of it: ATTENTIVE is
now one of the states the media panel ducks on (media-policy.js). Off
unless `follow_up_seconds` is given, so every older test here still
describes a reply ending at IDLE.

Only after her name (2026-10-08, `open_after_reply=False`, what `saathi
run` passes by default): live, a lecture playing in the room kept the
window open turn after turn -- every reply reopened it and every
sentence from the video counted as her follow-up. The owner wants her
name to start every turn. So the window after an ordinary reply is
off; the window after her name alone ("Saathi" ... "Yes?") stays,
because there she has just addressed Saathi. Lost: a shorter window
(the video still talks within any window) and a voice match (no
speaker model on the device).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import WSMsgType, web

from saathi.audio.capture import Capture
from saathi.audio.wake import Endpointer, vad_is_speech
from saathi.core import Core, Event, State
from saathi.identity.preferences import (
    CAPTIONS_KEY,
    LANGUAGE_KEY,
    TTS_BACKEND_KEY,
    read_preference,
    write_preference,
)
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from saathi.voice.tts.registry import DEFAULT_PREFERRED_BACKEND_ID, default_backends

_STATIC_DIR = Path(__file__).parent / "static"
_CAPTURE_CHUNK_BYTES = 3200  # 100ms of 16kHz mono 16-bit PCM
# Holding-card progress cadence: 20 updates over a 2 s hold is smooth
# enough to read as "it's doing something" without flooding the socket.
_HOLD_TICK_SECONDS = 0.1
# Live captions: how often what she has said so far is re-transcribed
# while the key is held. Each poll is one STT call on the whole buffer
# so far, and only runs while captions are on.
_PREVIEW_SECONDS = 1.0
# Wake word: how long the eyes show "you called me?" before listening.
_ATTENTIVE_SECONDS = 0.35
# Only her name, then quiet: how long before Saathi says "Yes?". Long
# enough to gather a thought, short enough not to feel ignored.
_NAME_PROMPT_SECONDS = 3.5
# Below this little of the wait left, "Yes?" is said at once rather
# than opening a window too short to start a sentence in.
_NAME_PROMPT_MIN_WAIT = 0.3

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


def _ms_since(monotonic_at: float | None) -> int | None:
    """Milliseconds since a `time.monotonic()` reading, or None."""
    if monotonic_at is None:
        return None
    return round((time.monotonic() - monotonic_at) * 1000)


def _mark_started_by_name(session) -> None:
    """Tells the session this turn began with her name, so a cloud
    transcript of just the name ("Saudi?") is read as the name, not as a
    question. Optional (cascade.py's `started_by_name`), like
    `preview_heard`: a session without it is unaffected."""
    mark = getattr(session, "started_by_name", None)
    if mark is not None:
        mark()


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
            # getattr, not attribute access: a session's timings object
            # predating the voice picker (or a test's own fake) lacks
            # these, and the broad except below would otherwise swallow
            # the *whole* row for three columns that are allowed to be
            # NULL anyway.
            voice=getattr(timings, "voice", None),
            tts_chars=getattr(timings, "tts_chars", None),
            tts_cost_usd=getattr(timings, "tts_cost_usd", None),
        )
    except Exception:
        logger.exception("failed to log turn")


async def _run_turn(
    session,
    core: Core,
    generation: int,
    turn_generation: dict,
    store,
    eou_ms: int | None,
    caption=None,
    on_done=None,
    on_name_only=None,
) -> None:
    """THINKING -> SPEAKING -> IDLE for one real turn. Runs the blocking
    STT/LLM/TTS work in an executor thread; every `core.handle()` call
    here still happens back on this event loop thread, in the `await`'s
    continuation, not inside the executor thread itself.

    `generation` is this turn's stamp, taken from `turn_generation` when
    it started. If a barge-in has since bumped the counter, this turn has
    been superseded and must not touch state on its way out — the press
    that superseded it already did.

    `on_done(generation)` (the open conversation) may take the turn's end
    instead of `done`; `on_name_only(generation)` takes a turn that was
    only her name -- see `build_app`."""
    loop = asyncio.get_running_loop()
    caption = caption or (lambda who, text: None)
    try:
        reply_text = await loop.run_in_executor(None, session.end_turn)
    except Exception:
        logger.exception("turn failed (STT or LLM)")
        heard = getattr(session, "last_heard", None)
        if heard:
            caption("her", heard)
        caption("saathi", _FALLBACK_REPLY_TEXT)
        await _speak_and_finish(
            session,
            core,
            generation,
            turn_generation,
            _FALLBACK_REPLY_TEXT,
            store,
            eou_ms,
            on_done=on_done,
        )
        return
    heard = getattr(session, "last_heard", None)
    if heard:
        caption("her", heard)
    if reply_text.strip():
        caption("saathi", reply_text)
    if not reply_text.strip():
        # Nothing was said (the session's speech gate, or an empty
        # transcript -- see cascade.py's end_turn()). THINKING -> IDLE
        # via no_response, never SPEAKING: no fallback line, no "I
        # didn't catch that". And no `turns` row: a turn is an exchange,
        # and this wasn't one -- logging it would put a row with no
        # STT, LLM or TTS time into the p95 the latency budget reads.
        if turn_generation["value"] != generation:
            return
        if on_name_only is not None and getattr(session, "heard_only_name", False):
            # Only her name: she is calling, not asking yet. No reply,
            # no model call (cascade.py) -- eyes on her, still listening.
            logger.info("only her name was said; listening for the rest")
            on_name_only(generation)
            return
        core.handle(Event("no_response"))
        logger.info("nothing said; turn ended silently")
        return
    logger.info("reply: %s", reply_text)

    await _speak_and_finish(
        session, core, generation, turn_generation, reply_text, store, eou_ms, on_done=on_done
    )


async def _speak_and_finish(
    session,
    core: Core,
    generation: int,
    turn_generation: dict,
    text: str,
    store=None,
    eou_ms: int | None = None,
    on_done=None,
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
    if on_done is None or not on_done(generation):
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
    # No preference written yet means the preferred default (Chirp), the
    # same thing cli.py hands the session -- not the offline fallback.
    current_backend = DEFAULT_PREFERRED_BACKEND_ID
    captions = False
    if store is not None:
        current_language = read_preference(store, LANGUAGE_KEY, DEFAULT_LANGUAGE)
        current_backend = read_preference(store, TTS_BACKEND_KEY, DEFAULT_PREFERRED_BACKEND_ID)
        captions = read_preference(store, CAPTIONS_KEY, "off") == "on"
    return json.dumps(
        {
            "type": "settings",
            "languages": list(SUPPORTED_LANGUAGES),
            "current_language": current_language,
            "backends": backends,
            "current_backend": current_backend,
            "captions": captions,
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
    wake=None,
    wake_endpointer=None,
    emotions=None,
    calls=None,
    family=None,
    follow_up_seconds: float | None = None,
    open_after_reply: bool = True,
    listen_window=None,
    name_prompt_seconds: float = _NAME_PROMPT_SECONDS,
) -> web.Application:
    app = web.Application()
    websockets: set[web.WebSocketResponse] = set()
    live_capture: dict[str, Capture | None] = {"capture": None}
    turn_generation = {"value": 0}
    turn_started_at: dict[str, float | None] = {"value": None}
    hold_task: dict[str, asyncio.Task | None] = {"task": None}
    preview_task: dict[str, asyncio.Task | None] = {"task": None}

    def _send_all(message: str) -> None:
        for ws in list(websockets):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(message))

    def caption(who: str, text: str) -> None:
        """A line of the transcript, to every screen -- only while the
        `captions` preference is on. Content, not status: what was
        actually heard and said, never \"Listening...\". Runs on the loop."""
        if store is None or read_preference(store, CAPTIONS_KEY, "off") != "on":
            return
        _send_all(json.dumps({"type": "caption", "who": who, "text": text}))

    async def run_preview() -> None:
        """Her words so far, captioned while she is still holding the
        key -- so the person demoing sees the mic is heard before the
        turn ends, not after. Stops at release (cancelled) or when the
        state leaves LISTENING. A failed poll is logged and the next one
        tried; it never touches the turn."""
        loop = asyncio.get_running_loop()
        last = ""
        while True:
            await asyncio.sleep(_PREVIEW_SECONDS)
            if core.state not in (State.LISTENING, State.ATTENTIVE):
                return
            try:
                heard = await loop.run_in_executor(None, session.preview_heard)
            except Exception:
                logger.exception("live caption failed")
                continue
            if core.state not in (State.LISTENING, State.ATTENTIVE):
                return
            if heard and heard != last:
                last = heard
                caption("her", heard)

    def start_preview() -> None:
        if getattr(session, "preview_heard", None) is None:
            return
        if store is None or read_preference(store, CAPTIONS_KEY, "off") != "on":
            return
        stop_preview()
        preview_task["task"] = asyncio.get_running_loop().create_task(run_preview())

    def stop_preview() -> None:
        task = preview_task.pop("task", None)
        if task is not None:
            task.cancel()

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
    # The turn that showed the card now up (None: no turn -- shown
    # between turns, e.g. by initiative). Read from the executor thread
    # inside end_turn(), where the counter is stable for the turn.
    card_shown_in: dict[str, int | None] = {"generation": None}
    # The turn that last changed the media (play, pause, volume...).
    media_touched_in: dict[str, int | None] = {"generation": None}

    def broadcast_threadsafe(payload: dict) -> None:
        if payload.get("type") == "card" and payload.get("card") is not None:
            card_shown_in["generation"] = turn_generation["value"]
        if payload.get("type") == "media":
            media_touched_in["generation"] = turn_generation["value"]
        loop = loop_holder["loop"]
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(_send_all, json.dumps(payload))

    def end_turn_answered_on_screen() -> None:
        """Her tap answered the question the live turn is asking, so the
        turn is over -- see the module docstring. Only `core.py` moves
        the state: here it's asked for the one event that fits."""
        if session is None:
            return
        if core.state == State.SPEAKING:
            # The reply reading her the options is pointless now. Stop
            # it; say() returns and the turn's own tail fires `done`.
            session.interrupt()
        elif core.state == State.THINKING:
            # The reply hasn't been spoken yet (the model is still
            # composing it, or TTS is warming up). Supersede the turn so
            # its tail never speaks, and close the state machine: there
            # is nothing to say.
            turn_generation["value"] += 1
            core.handle(Event("no_response"))

    # `family` (call/family_runtime.py): the family-app call's device
    # end -- `{"type": "rtc", ...}` both ways between this page and
    # call/webrtc.py; the server only carries it.
    seams = [obj for obj in (media, cards, emotions, calls, family) if obj is not None]
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
            # `hold.holding` goes False on abandon() -- including the
            # abandon inside hold.clear() while a press is still down
            # (the other party hung up first). Without this exit the
            # task would tick forever and block every later hold.
            while hold.holding:
                await asyncio.sleep(_HOLD_TICK_SECONDS)
                if hold.tick(time.monotonic() - started_at):
                    return
        finally:
            hold_task["task"] = None

    # Which way the press that is currently down went: "hold" or
    # "core". A release is routed the same way as its press, whatever
    # `hold.active` says by then -- a handler set (or cleared) while
    # the key is down must not strand core.py in LISTENING with the
    # capture running, or hand core.py a release it never saw a press
    # for. Found in review.
    press_route: dict[str, str | None] = {"value": None}

    def begin_capture(on_chunk, preroll: bytes = b"", follow=None, on_complete=None) -> None:
        """Opens the mic for the turn just started (LISTENING, or
        ATTENTIVE on the wake path). Shared by the spacebar and the
        wake word; only the trigger differs. `preroll` is audio already
        heard that belongs to this turn, fed before the live mic.
        `follow` (wake path) takes over the wake listener's own mic
        instead of starting another `parec`: no gap mid-sentence, and
        it feeds what arrived while the name was being checked itself."""
        session.start()
        turn_started_at["value"] = time.monotonic()
        if preroll:
            on_chunk(preroll)
        if follow is not None:
            live_capture["capture"] = follow(on_chunk, on_complete)
        else:
            capture = Capture(capture_source_id, on_chunk, _CAPTURE_CHUNK_BYTES)
            capture.start()
            live_capture["capture"] = capture
        start_preview()

    def end_listening(waited_ms: int | None = None, quiet_since: float | None = None) -> None:
        """LISTENING -> THINKING and the turn runs: on the key's release,
        or when a hands-free turn's endpointer says she's finished.

        `turns.eou_ms` is the time from the turn's start to here -- how
        long she was listened to, not how long the device waited after
        her last word (tests/test_screen_server.py pins that meaning).
        `waited_ms`, the hands-free wait after her last word, goes to
        the log instead, so a slow endpointer is visible. Under the old
        2 s audio bursts (audio/capture.py) every eou_ms was a multiple
        of ~2,000 for exactly that reason."""
        if not core.handle(Event("release")):
            return
        if session is None:
            core.handle(Event("no_response"))  # fake: no AI at checkpoint 1
            return
        stop_preview()
        capture = live_capture.pop("capture", None)
        if capture is not None:
            capture.stop()
        eou_ms = None
        started_at = turn_started_at["value"]
        if started_at is not None:
            eou_ms = round((time.monotonic() - started_at) * 1000)
        if waited_ms is not None:
            logger.info("hands-free turn ended %d ms after her last word", waited_ms)
        turn_generation["value"] += 1
        generation = turn_generation["value"]
        # When she last spoke (or the turn began, if she never did): how
        # long she has already been quiet if the turn was only her name.
        last_quiet["generation"], last_quiet["at"] = generation, quiet_since
        asyncio.get_running_loop().create_task(
            _run_turn(
                session,
                core,
                generation,
                turn_generation,
                store,
                eou_ms,
                caption=caption,
                on_done=open_conversation,
                on_name_only=heard_only_name,
            )
        )

    # The wake seam. `wake` is audio/wake.py's WakeWordListener, or
    # anything built the same way: `wake(on_wake, should_listen)`
    # returns an object with start()/stop(). It hears the name on a
    # worker thread; everything it causes happens here, on the loop,
    # through core.py like a press would -- IDLE -> ATTENTIVE (the eyes
    # perk up) -> LISTENING, then the endpointer ends the turn, since
    # there is no key to let go of.
    def on_wake(pcm: bytes, rest: str, after=None, follow=None, last_speech_at=None) -> None:
        if session is None or capture_source_id is None:
            return
        # Taken first: once the state leaves IDLE the listener stops
        # collecting and clears it. With `follow`, which hands over
        # everything after the name, the preroll is the name's own
        # audio: the cloud transcript must contain the name for the
        # turn to count (cascade.py, `mentions_name`). Lost: leaving
        # it out -- the cloud then never heard the name and the second
        # check had nothing to confirm.
        if follow is not None:
            preroll = pcm if not rest else b""
        else:
            preroll = after() if after is not None and not rest else b""
        if core.state == State.SLEEPING:
            core.handle(Event("wake"))
        if not core.handle(Event("notice")):
            return  # not idle any more: a press got there first
        loop = asyncio.get_running_loop()
        if rest:
            # "Saathi, play a song" in one breath: the request is already
            # in this audio. Run it as the turn, no further listening.
            session.start()
            _mark_started_by_name(session)
            session.send_audio(pcm)
            turn_started_at["value"] = time.monotonic()
            core.handle(Event("confirm"))
            end_listening(_ms_since(last_speech_at))
            return
        endpointer = wake_endpointer()

        def on_chunk(chunk: bytes) -> None:
            session.send_audio(chunk)
            if endpointer.push(chunk):
                loop.call_soon_threadsafe(end_hands_free, endpointer)

        def on_complete() -> None:
            # The wake listener heard a whole command at a short pause
            # ("call Udhi"): end now, not at the endpointer's long one.
            loop.call_soon_threadsafe(end_hands_free, endpointer)

        live_endpointer["value"] = endpointer
        begin_capture(on_chunk, preroll, follow=follow, on_complete=on_complete)
        _mark_started_by_name(session)
        # ATTENTIVE long enough for the eyes to show they heard her
        # name; the mic is already open, so nothing she says is lost.
        loop.call_later(_ATTENTIVE_SECONDS, confirm_attention)

    def confirm_attention() -> None:
        if core.state == State.ATTENTIVE:
            core.handle(Event("confirm"))

    def end_hands_free(endpointer) -> None:
        if live_endpointer["value"] is not endpointer:
            return  # a stale signal from a turn already ended
        live_endpointer["value"] = None
        if core.state == State.ATTENTIVE:
            core.handle(Event("confirm"))
        if core.state == State.LISTENING:
            last_speech_at = getattr(endpointer, "last_speech_at", None)
            end_listening(
                _ms_since(last_speech_at), quiet_since=last_speech_at or turn_started_at["value"]
            )

    # The open conversation (SPEC.md, "Triggers": once a conversation is
    # open, follow-ups need no trigger). After a spoken reply the eyes
    # stay on her (ATTENTIVE) and a ListenWindow (audio/listen_window.py)
    # listens hands-free: speech starting inside the window is the next
    # turn, a quiet window closes the conversation (IDLE, the name is
    # needed again). The window's mic is its own `Capture`; nothing goes
    # to the session until she actually starts.
    window_holder: dict[str, dict | None] = {"entry": None}
    last_quiet: dict = {"generation": None, "at": None}
    if listen_window is None:

        def listen_window(seconds: float):
            from saathi.audio.listen_window import ListenWindow

            return ListenWindow(vad_is_speech(), window_seconds=seconds)

    def close_window() -> None:
        entry = window_holder["entry"]
        window_holder["entry"] = None
        if entry is None:
            return
        with entry["lock"]:
            entry["closed"] = True
        capture = entry["capture"]
        if capture is not None and live_capture.get("capture") is not capture:
            capture.stop()

    def open_window(seconds: float, on_lapse) -> None:
        close_window()
        loop = asyncio.get_running_loop()
        window = listen_window(seconds)
        entry: dict = {"window": window, "lock": threading.Lock(), "closed": False}

        def on_chunk(chunk: bytes) -> None:
            with entry["lock"]:
                if entry["closed"]:
                    return
                was_started = window.started
                event = window.push(chunk)
                if was_started:
                    session.send_audio(chunk)
                elif event == "start":
                    session.start()
                    session.send_audio(window.started_audio())
                if event == "start":
                    loop.call_soon_threadsafe(window_started, entry)
                    if window.ended:
                        event = "end"
                if event == "end":
                    entry["closed"] = True
                    loop.call_soon_threadsafe(window_ended, entry)
                elif event == "lapse":
                    entry["closed"] = True
                    loop.call_soon_threadsafe(window_lapsed, entry, on_lapse)

        entry["capture"] = Capture(capture_source_id, on_chunk, _CAPTURE_CHUNK_BYTES)
        window_holder["entry"] = entry
        entry["capture"].start()

    def window_started(entry: dict) -> None:
        if window_holder["entry"] is not entry:
            return
        if core.state == State.ATTENTIVE:
            core.handle(Event("confirm"))
        if core.state != State.LISTENING:
            close_window()
            return
        turn_started_at["value"] = time.monotonic()
        live_capture["capture"] = entry["capture"]
        start_preview()

    def window_ended(entry: dict) -> None:
        if window_holder["entry"] is not entry:
            return
        window_holder["entry"] = None
        if core.state == State.LISTENING:
            last_speech_at = entry["window"].last_speech_at
            end_listening(
                _ms_since(last_speech_at), quiet_since=last_speech_at or turn_started_at["value"]
            )

    def window_lapsed(entry: dict, on_lapse) -> None:
        if window_holder["entry"] is not entry:
            return
        close_window()
        if core.state == State.ATTENTIVE:
            on_lapse()

    def conversation_can_stay_open(generation: int) -> bool:
        if follow_up_seconds is None or follow_up_seconds <= 0:
            return False
        if session is None or capture_source_id is None:
            return False
        if calls is not None and calls.current_id is not None:
            return False  # a call is on: its audio is not for her turn
        if hold is not None and hold.active:
            return False
        if (
            media is not None
            and media_touched_in["generation"] == generation
            and getattr(media, "playing", False)
            and not getattr(media, "paused", False)
        ):
            # The reply was a media command ("louder", "next") and the
            # song is playing: the action was the answer, and a window
            # would hold the song ducked for seconds right after she
            # asked to hear it. Closed; her name opens the next one.
            return False
        return True

    def open_conversation(generation: int, *, after_her_name: bool = False) -> bool:
        """`on_done` for every spoken reply: SPEAKING -> ATTENTIVE with
        the window open, or False (the caller sends `done`). With
        `open_after_reply` off, only the "Yes?" after her name opens it."""
        if not open_after_reply and not after_her_name:
            return False
        if not conversation_can_stay_open(generation):
            return False
        if not core.handle(Event("follow_up")):
            return False
        open_window(follow_up_seconds, close_conversation)
        return True

    def close_conversation() -> None:
        core.handle(Event("dismiss"))

    def heard_only_name(generation: int) -> None:
        """The turn was only her name ("Saathi", heard by the cloud as
        "Saudi?"). She is calling, not asking: eyes on her (ATTENTIVE),
        listening; if she stays quiet for `name_prompt_seconds`, a short
        "Yes?" out loud, then the usual open window."""
        if follow_up_seconds is None or capture_source_id is None:
            core.handle(Event("no_response"))
            return
        if not core.handle(Event("name_only")):
            return
        # Counted from when she went quiet, not from now: the turn already
        # waited for her pause and its transcript.
        since = last_quiet["at"] if last_quiet["generation"] == generation else None
        remaining = name_prompt_seconds - (time.monotonic() - since if since else 0.0)
        if remaining <= _NAME_PROMPT_MIN_WAIT:
            prompt_her(generation)
            return
        open_window(remaining, lambda: prompt_her(generation))

    def prompt_her(generation: int) -> None:
        if turn_generation["value"] != generation:
            return
        if not core.handle(Event("prompt")):
            return
        name_prompt = getattr(session, "name_prompt", None)
        line = name_prompt() if name_prompt is not None else "Yes?"
        turn_generation["value"] += 1
        asyncio.get_running_loop().create_task(
            _speak_and_finish(
                session,
                core,
                turn_generation["value"],
                turn_generation,
                line,
                None,  # not an exchange: no `turns` row
                None,
                on_done=lambda g: open_conversation(g, after_her_name=True),
            )
        )

    async def close_window_on_cleanup(_app: web.Application) -> None:
        close_window()

    app.on_cleanup.append(close_window_on_cleanup)

    live_endpointer: dict[str, object | None] = {"value": None}
    if wake_endpointer is None:

        def wake_endpointer():
            if follow_up_seconds:
                # Only her name and then nothing: the turn ends when it
                # is time for "Yes?", not at the endpointer's 5 s.
                return Endpointer(vad_is_speech(), no_speech_seconds=name_prompt_seconds)
            return Endpointer(vad_is_speech())

    if wake is not None:
        listener_holder: dict[str, object | None] = {"listener": None}

        async def start_wake(_app: web.Application) -> None:
            loop = asyncio.get_running_loop()

            def on_wake_threadsafe(pcm: bytes, rest: str, after=None, **handover) -> None:
                loop.call_soon_threadsafe(lambda: on_wake(pcm, rest, after, **handover))

            # Deaf to her name while a call is on screen (Twilio or the
            # family app -- both drive the call panel): "Saathi" said to
            # her son mid-call would otherwise start a turn that talks
            # over him. Found in review, 2026-10-08.
            def should_listen() -> bool:
                if calls is not None and calls.current_id is not None:
                    return False
                return core.state in (State.IDLE, State.SLEEPING)

            listener = wake(on_wake_threadsafe, should_listen)
            listener.start()
            listener_holder["listener"] = listener

        async def stop_wake(_app: web.Application) -> None:
            listener = listener_holder.pop("listener", None)
            if listener is not None:
                listener.stop()

        app.on_startup.append(start_wake)
        app.on_cleanup.append(stop_wake)

    async def index(_request: web.Request) -> web.FileResponse:
        return web.FileResponse(_STATIC_DIR / "index.html")

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        websockets.add(ws)
        await ws.send_str(json.dumps({"type": "state", "state": core.state.value}))
        await ws.send_str(_settings_message(store))
        if cards is not None and cards.current is not None:
            # A card stays until it is answered or dismissed, across a
            # reload too: the server holds the question, the browser
            # only draws it.
            await ws.send_str(json.dumps({"type": "card", "card": cards.current.as_message()}))
        if calls is not None:
            call_message = calls.message()
            if call_message is not None:
                await ws.send_str(json.dumps(call_message))
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
                    if key not in (LANGUAGE_KEY, TTS_BACKEND_KEY, CAPTIONS_KEY) or not isinstance(
                        value, str
                    ):
                        logger.warning("dropped malformed set_preference: %r", payload)
                        continue
                    if key == CAPTIONS_KEY and value not in ("on", "off"):
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
                    code = payload.get("code")
                    if event == "error":
                        # Never silent: the code is the player's own
                        # (150/101 embedding disabled, 100 not found,
                        # "no_ready"/"api" from the panel's timeouts).
                        logger.warning("player error for %s: code %s", video_id, code)
                    if media is not None and isinstance(event, str):
                        media.on_browser_event(
                            event,
                            video_id if isinstance(video_id, str) else None,
                            code=code if isinstance(code, (str, int)) else None,
                        )
                    continue
                if payload.get("type") == "card_answer":
                    # A tap. The same entry point a voice answer uses
                    # (CardController.answer); a stale id or a malformed
                    # answer is dropped there, not here -- but said in
                    # the log, so "I tapped and nothing happened" can be
                    # traced to the card having been replaced.
                    if cards is not None:
                        card_id = payload.get("id")
                        if isinstance(card_id, str):
                            if cards.answer(card_id, payload.get("answer"), source="tap"):
                                if card_shown_in["generation"] == turn_generation["value"]:
                                    # call_soon, so the card's own clear (queued
                                    # by the seam) reaches the screen before
                                    # the state change that follows from it.
                                    asyncio.get_running_loop().call_soon(
                                        end_turn_answered_on_screen
                                    )
                            else:
                                logger.info("tap on %s dropped: not the current card", card_id)
                    continue
                if payload.get("type") == "rtc":
                    # The kiosk's half of a family-app call (offer, ICE,
                    # connected/failed). Validated in call/webrtc.py.
                    if family is not None:
                        family.on_device_message(payload)
                    continue
                if payload.get("type") == "call_hangup":
                    # The phone panel's End call button. Not a hang-up
                    # here: the same registered hold handler a 2 s hold
                    # fires, and only for the call the panel is showing.
                    call_id = payload.get("id")
                    if (
                        calls is not None
                        and hold is not None
                        and isinstance(call_id, str)
                        and call_id == calls.current_id
                        and hold.active
                    ):
                        task = hold_task["task"]
                        if task is not None:
                            task.cancel()
                            hold_task["task"] = None
                        hold.complete()
                    else:
                        logger.info("end-call tap dropped: no such call on screen")
                    continue
                if payload.get("type") != "input":
                    continue
                kind = payload.get("event")
                if kind == "press":
                    press_route["value"] = "hold" if hold is not None and hold.active else "core"
                routed_to_hold = press_route["value"] == "hold"
                if kind == "release":
                    press_route["value"] = None
                if routed_to_hold:
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
                    was_attentive = core.state == State.ATTENTIVE
                    transitioned = core.handle(Event("press"))
                    if transitioned and was_attentive:
                        # A press during an open window (or the wake
                        # path's moment of attention): the key wins.
                        # Whatever mic that window had is let go first.
                        close_window()
                        stop_preview()
                        live_endpointer["value"] = None
                        old_capture = live_capture.pop("capture", None)
                        if old_capture is not None:
                            old_capture.stop()
                    if transitioned and session is not None and capture_source_id is not None:
                        if was_speaking:
                            # Barge-in: this press just superseded whatever
                            # turn was still speaking. Bump the generation
                            # *before* interrupting it, so its tail end
                            # (still unwinding in another thread) sees it's
                            # been superseded the moment it checks.
                            turn_generation["value"] += 1
                            session.interrupt()
                        begin_capture(_make_on_chunk(session))
                elif kind == "release":
                    end_listening()
        finally:
            websockets.discard(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket_handler)
    if family is not None:
        # Ctrl+P's pairing QR: loopback-only, never on the tunnelled app.
        family.install_local_routes(app)
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
    wake=None,
    emotions=None,
    calls=None,
    family=None,
    follow_up_seconds: float | None = None,
    open_after_reply: bool = True,
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
            wake=wake,
            emotions=emotions,
            calls=calls,
            family=family,
            follow_up_seconds=follow_up_seconds,
            open_after_reply=open_after_reply,
        ),
        host=host,
        port=port,
        print=None,
    )
