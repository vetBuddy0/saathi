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
Since 2026-10-08 every `play` also carries `target` ("embed" or
"browser") and `watch_url`: the face page's panel plays "embed" and
ignores "browser"; a client that can show the real youtube.com page
(the Android shell, on its own `/ws` connection) plays "browser" and
reports its failures as `media_event` errors with code "browser" or
"wall". A `media_event` may name which player is reporting (`target`:
"embed" or "browser"; the face page's frames predate the field and
carry none, which the controller reads as the embed's), so a `reset`
from one client cannot un-play what the other is playing. This server
tells the two apart by nothing: it broadcasts every message to every
client and hands every report to the controller, which owns the rule
(`tools/media.py`).

A second path, `/audio` (2026-10-08), is the Android shell as the
microphone and the speaker while the engine stays here -- see
`audio/remote.py` for why that split and not the engine in the APK.
One client at a time; a new connection replaces the old. Its frames:
client -> server text `{"type": "hello", "client": ..., "sample_rate":
16000}` on connect, then binary frames of raw PCM16 mono 16 kHz mic
audio (about 100 ms each), which the server forwards to the session
only between a press and its release on `/ws`, whatever the client
sends in between; server -> client text `{"type": "play", "id": ...,
"format": "wav"}` followed by exactly one binary frame holding one
complete WAV (one TTS sentence), answered by text `{"type": "played",
"id": ...}` once it has played out; and server -> client text
`{"type": "stop"}`, which the client answers with `played` for whatever
it cut short. Since the engine also runs inside the phone (the same
day, `SAATHI_AUDIO=remote` in cli.py): server -> client text `{"type":
"synthesize", "id": ..., "text": ..., "language": ...}` asks the client
to render one sentence with the phone's own text-to-speech, answered
by text `{"type": "synthesized", "id": ...}` followed at once by one
binary frame holding the WAV (nothing may come between the two: the
text frame is what tells the server the next binary frame is not
microphone audio), or by `{"type": "synthesized", "id": ..., "error":
...}` with no binary frame when it could not. This handler routes it
to `RemoteAudio.on_synthesized`; what it means for the turn is
`voice/tts/remote_backend.py`'s. While a client is attached, a press
starts no `parec` capture and TTS goes to the client, not the local
sink; with none, the device behaves exactly as it did before this path
existed. The route is only registered when `build_app` is given a
`remote_audio`. Both
sockets are opened with a heartbeat (`_HEARTBEAT_SECONDS`), so a client
that dies without a close frame is gone within seconds, not whenever
TCP gives up: on `/audio` that is what hands the mic and the speaker
back to the local hardware; on `/ws` it is what lets a press whose
socket died be released -- a socket that vanishes with its press down
(no close frame, or a page going away) has that press released on the
way out (`websocket_handler`'s `finally`), as a touch cancel is a
release on the shell; a socket that closes in order is trusted to have
released first, as the shell does.

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

Calls over the device's SIM (2026-10-28, `phone`, call/device_phone.py):
`{"type": "phone", "action": "dial", "number": ...}` (server -> every
screen; the face page ignores it, the Android shell places the call)
rides the broadcast seam too. `{"type": "phone_status", "can_call":
bool}` (shell -> server, on every open) is the shell saying whether it
can; a closed socket takes its yes with it. The server only carries both.
With Twilio from the face page (`call/web_phone.py`) the same frames carry
`"via": "twilio"`, the page reports `{"type": "phone_event", ...}`, and
`GET /phone/token` hands it a Twilio access token -- to loopback callers
only: the token can place calls, and the page that may use it is the one
on this device (a LAN address isn't a secure context, so no microphone).

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

The family inbox (2026-10-29, `inbox`, cloud/inbox.py): a message her
caregivers sent from their app is said between turns, word for word,
the way a reminder is, and her next words go back to them as the
reply. Off unless `build_app` is given an inbox; nothing crosses this
socket for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import WSCloseCode, WSMsgType, web

from saathi.audio.capture import Capture
from saathi.audio.wake import Endpointer, vad_is_speech
from saathi.core import Core, Event, State
from saathi.screen.cards import confirm
from saathi.identity.preferences import (
    CAPTIONS_KEY,
    LANGUAGE_KEY,
    TTS_BACKEND_KEY,
    WAKE_WORD_KEY,
    read_preference,
    write_preference,
)
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from saathi.voice.tts.registry import DEFAULT_PREFERRED_BACKEND_ID, default_backends

_STATIC_DIR = Path(__file__).parent / "static"
_CAPTURE_CHUNK_BYTES = 3200  # 100ms of 16kHz mono 16-bit PCM
# aiohttp pings every client this often and closes the socket when the
# pong is half that late. Without it a client that dies without a close
# frame (the phone off Wi-Fi, the app killed) stays "attached" for as
# long as the kernel keeps the TCP connection: on /audio every press
# would use the dead remote mic and every sentence would wait its bound
# in silence; on /ws a press left down would never be released (see
# websocket_handler). Five seconds: a phone's audio pipeline and a Wi-Fi
# hop both answer a ping in milliseconds, so a missed pong is a gone
# client, and it costs a sleeping tablet nothing it can notice.
_HEARTBEAT_SECONDS = 5.0
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
# How often due reminders are looked for (initiative/reminders.py). A
# reminder is said up to this late; its repeats are minutes apart.
_REMINDER_TICK_SECONDS = 30.0
# A card left up between turns (a song choice nobody answered) holds a
# due reminder back this long, then the reminder takes the screen:
# nothing on a card times out, and a forgotten one must not withhold a
# dose.
_STALE_CARD_SECONDS = 600.0

# What the model is told about a reminder it just said, in the window.
_REMINDER_NOTE = (
    "a reminder she set. When she says she has done it, call reminder_done "
    "before you reply"
)

# For tests: one tick of the reminder lane, run now.
CHECK_REMINDERS = web.AppKey("check_reminders", object)
_REMINDER_TASK = web.AppKey("reminder_task", asyncio.Task)

# The clock's timers (tools/clock.py) are looked at this often: "your
# tea timer is done" is said within a second of it, when she is free.
_CLOCK_TICK_SECONDS = 1.0
# A timer that ran out during a call or a long turn is still said once
# she is free, up to this long after; later than that, the screen has
# shown it and the words would only puzzle her.
_TIMER_SAY_WITHIN = 300.0
_TIMER_NOTE = "the timer she set has run out; the clock beside you shows it"
CHECK_CLOCK = web.AppKey("check_clock", object)
_CLOCK_TASK = web.AppKey("clock_task", asyncio.Task)

# The family inbox (cloud/inbox.py): how often the cloud is asked for
# messages to read her (and her caregivers' reminders are synced).
_INBOX_TICK_SECONDS = 10.0
_MESSAGE_NOTE = (
    "you read her a message from her family, in their words. What she says next is "
    "sent to them as her reply"
)
# For tests: one tick of the inbox lane, awaited.
CHECK_INBOX = web.AppKey("check_inbox", object)
_INBOX_TASK = web.AppKey("inbox_task", asyncio.Task)

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
    record=None,
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
    record = record or (lambda heard, reply: None)
    try:
        reply_text = await loop.run_in_executor(None, session.end_turn)
    except Exception:
        logger.exception("turn failed (STT or LLM)")
        heard = getattr(session, "last_heard", None)
        if heard:
            caption("her", heard)
            record(heard, _FALLBACK_REPLY_TEXT)
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
        if heard:
            record(heard, reply_text)
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
    phone=None,
    admin_info=None,
    follow_up_seconds: float | None = None,
    open_after_reply: bool = True,
    listen_window=None,
    name_prompt_seconds: float = _NAME_PROMPT_SECONDS,
    remote_audio=None,
    cloud=None,
    reminder_tick_seconds: float | None = _REMINDER_TICK_SECONDS,
    clock=None,
    clock_tick_seconds: float | None = _CLOCK_TICK_SECONDS,
    inbox=None,
    inbox_tick_seconds: float | None = _INBOX_TICK_SECONDS,
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

    def record(heard: str, reply: str) -> None:
        """One exchange to the cloud copy (cloud/sync.py), with the clip
        the transcriber heard. Only queues: the upload never delays her
        reply."""
        if inbox is not None:
            reply_heard(heard)  # her answer to a family message, if it was one
        if cloud is not None:
            cloud.record_turn(heard, reply, audio=getattr(session, "last_audio", None))

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

    # The tablet's wake word ("Melody": the Android shell's keyword
    # spotter, on the device). The shell owns the microphone and the
    # spotting; whether it may listen is decided here, where the turn
    # and the calls are known: her setting on, the engine idle or
    # asleep (never while she is listening, being answered, or spoken
    # to), and no call on screen -- the call's page needs the mic, and
    # "Melody" said to her son mid-call must not start a turn. Sent as
    # `{"type": "wake", "listen": bool}` only to sockets that asked
    # (`{"type": "subscribe", "wake": true}`), so the face page's
    # stream is unchanged.
    wake_subscribers: set[web.WebSocketResponse] = set()
    wake_listen: dict[str, bool | None] = {"value": None}

    def wake_allowed() -> bool:
        if session is None:
            return False
        if store is not None and read_preference(store, WAKE_WORD_KEY, "on") == "off":
            return False
        if calls is not None and calls.current_id is not None:
            return False
        return core.state in (State.IDLE, State.SLEEPING)

    def refresh_wake() -> None:
        allowed = wake_allowed()
        if allowed == wake_listen["value"]:
            return
        wake_listen["value"] = allowed
        message = json.dumps({"type": "wake", "listen": allowed})
        for ws in list(wake_subscribers):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(message))

    def broadcast_state(state, _event: Event) -> None:
        _send_all(json.dumps({"type": "state", "state": state.value}))
        refresh_wake()

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
        if payload.get("type") == "call":
            loop.call_soon_threadsafe(refresh_wake)

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
    seams = [
        obj for obj in (media, cards, emotions, calls, family, phone, clock) if obj is not None
    ]
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
    # Which socket's press is down (None between holds): set when a press
    # takes effect, cleared by the release, real or synthesised -- see
    # websocket_handler's `finally`.
    press_owner: dict[str, web.WebSocketResponse | None] = {"ws": None}

    def handle_input(kind: str, ws: web.WebSocketResponse, by_name: bool = False) -> None:
        """One press or release from `ws` -- or the release the server
        synthesises for a socket that closed with its press down. Every
        press and release goes through here, so the two cannot drift.
        `by_name`: the tablet heard its wake word and pressed for her;
        the turn's audio starts with the name, and the cloud transcript
        must have it too (cascade.py's `mentions_name`) or the turn ends
        silently -- the same second check as the laptop's wake path."""
        if kind == "press":
            press_route["value"] = "hold" if hold is not None and hold.active else "core"
        routed_to_hold = press_route["value"] == "hold"
        if kind == "release":
            press_route["value"] = None
            press_owner["ws"] = None
        if routed_to_hold:
            # The button means "hold to confirm" for now; core.py
            # never sees this press. See the hold seam above.
            if kind == "press" and hold_task["task"] is None:
                hold.begin()
                press_owner["ws"] = ws
                hold_task["task"] = asyncio.get_running_loop().create_task(
                    run_hold(time.monotonic())
                )
            elif kind == "release":
                task = hold_task["task"]
                if task is not None:
                    task.cancel()
                    hold_task["task"] = None
                hold.abandon()
            return
        if kind == "press":
            # Only act if core.py actually transitioned — e.g. a
            # press while IDLE/SLEEPING/SPEAKING with no session
            # configured has no transition and must not start a
            # capture. See core.py's module docstring for the bug
            # that taught us this the first time.
            was_speaking = core.state == State.SPEAKING
            was_attentive = core.state == State.ATTENTIVE
            transitioned = core.handle(Event("press"))
            if transitioned:
                press_owner["ws"] = ws
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
            # The phone as the microphone: with an /audio client
            # attached the mic is its frames, not parec's -- and
            # a device with no usable local mic can still hold a
            # turn through it.
            remote_mic = remote_audio is not None and remote_audio.attached
            if (
                transitioned
                and session is not None
                and (capture_source_id is not None or remote_mic)
            ):
                if was_speaking:
                    # Barge-in: this press just superseded whatever
                    # turn was still speaking. Bump the generation
                    # *before* interrupting it, so its tail end
                    # (still unwinding in another thread) sees it's
                    # been superseded the moment it checks.
                    turn_generation["value"] += 1
                    session.interrupt()
                begin_capture(_make_on_chunk(session))
                if by_name:
                    _mark_started_by_name(session)
        elif kind == "release":
            end_listening()

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
        elif remote_audio is not None and remote_audio.attached:
            remote_audio.start_listening(on_chunk)
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
        if remote_audio is not None:
            # Idempotent, and asked for whichever way the press went: a
            # client that attached or left while the key was down must
            # not leave the sink open past this release.
            remote_audio.stop_listening()
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
                record=record,
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

    # -- The reminder lane (initiative/reminders.py) --------------------
    # Speaks first only between turns: IDLE or SLEEPING, no call, no
    # hold, and no other card up (for long). The policy log records why
    # each one fired (SPEC: every proactive utterance records why);
    # presence is taken as confirmed, since nothing on the tablet can
    # sense her yet and treating "unknown" as "absent" would mean no
    # reminder ever fires. An empty room gets the repeats, then a
    # `missed`. The noticed/social lane stays off: it changes what she
    # hears unprompted, and is the owner's call.
    reminder_cards: set[str] = set()
    stale_card: dict[str, object] = {"id": None, "since": 0.0}

    def _card_in_the_way() -> bool:
        current = cards.current if cards is not None else None
        if current is None or current.id in reminder_cards:
            stale_card["id"] = None
            return False
        if stale_card["id"] != current.id:
            stale_card["id"], stale_card["since"] = current.id, time.monotonic()
        return time.monotonic() - float(stale_card["since"]) < _STALE_CARD_SECONDS

    def free_to_speak_first() -> bool:
        return (
            session is not None
            and core.state in (State.IDLE, State.SLEEPING)
            and not (calls is not None and calls.current_id is not None)
            and not (hold is not None and hold.active)
        )

    def say_first(line: str, why: str) -> None:
        """Says `line` unprompted, once `core` has taken the "initiative"
        event, then opens the mic as after "Yes?". Shared by reminders
        and the clock's timers."""
        caption("saathi", line)
        turn_generation["value"] += 1
        generation = turn_generation["value"]

        async def speak() -> None:
            await _speak_and_finish(
                session,
                core,
                generation,
                turn_generation,
                line,
                None,  # not an exchange: no `turns` row
                None,
                # SPEC: after speaking unprompted, the mic opens without a
                # press -- the same window as after "Yes?".
                on_done=lambda g: open_conversation(g, after_her_name=True),
            )
            said_first = getattr(session, "said_first", None)
            if said_first is not None:
                said_first(line, why)

        asyncio.get_running_loop().create_task(speak())

    def check_reminders(now: datetime | None = None) -> list:
        """One tick: say what is due, give up on what went unanswered.
        Returns the reminder actions found (for tests and the log)."""
        path = getattr(store, "path", None)
        if path is None or not free_to_speak_first():
            return []
        if _card_in_the_way():
            return []
        from saathi.identity.store import IdentityStore
        from saathi.initiative import reminders as reminder_rules
        from saathi.initiative.policy import PolicyContext, evaluate
        from saathi.initiative.scheduler import InitiativeCandidate

        now = now or datetime.now(timezone.utc)
        with IdentityStore(path) as own:
            repeat_after, attempts = reminder_rules.settings(own)
            actions = reminder_rules.due(own, now, repeat_after=repeat_after, attempts=attempts)
            for action in actions:
                if action.kind == "miss":
                    reminder_rules.record(own, action, "missed", now)
                    logger.info(
                        "reminder %d missed after %d tries: %r",
                        action.reminder_id,
                        action.attempt,
                        action.text,
                    )
            by_reason = {
                f"Reminder due: {action.text} [reminder_id={action.reminder_id}]": action
                for action in actions
                if action.kind == "fire"
            }
            if not by_reason:
                return actions
            candidates = [
                InitiativeCandidate(kind="scheduled", reason=reason) for reason in by_reason
            ]
            rows = evaluate(own, candidates, PolicyContext(presence=True), now=now)
            allowed = [
                by_reason[row["reason"]]
                for row in rows
                if row["suppressed_by"] is None and row["reason"] in by_reason
            ]
            if not allowed or not core.handle(Event("initiative")):
                return actions
            for action in allowed:
                reminder_rules.record(own, action, "fired", now)
        line = reminder_rules.line(allowed)
        logger.info("reminder said: %r", line)
        if cards is not None:
            reminder_cards.add(cards.show(confirm(reminder_rules.card_title(allowed), spoken=line)))
        say_first(line, _REMINDER_NOTE)
        return actions

    app[CHECK_REMINDERS] = check_reminders

    def reminder_answered(result) -> None:
        """The card's Yes is "done"; No or a dismissal leaves it to repeat."""
        if result.card_id not in reminder_cards:
            return
        reminder_cards.discard(result.card_id)
        path = getattr(store, "path", None)
        if result.yes is not True or path is None:
            return
        from saathi.identity.store import IdentityStore
        from saathi.initiative import reminders as reminder_rules

        with IdentityStore(path) as own:
            done = reminder_rules.acknowledge(own, datetime.now(timezone.utc))
        logger.info("reminder done (%s): %s", result.source, [action.text for action in done])

    if cards is not None:
        cards.on_answer(reminder_answered)

    if reminder_tick_seconds:

        async def reminder_ticks() -> None:
            while True:
                await asyncio.sleep(reminder_tick_seconds)
                try:
                    check_reminders()
                except Exception:
                    logger.exception("reminder tick failed")

        async def start_reminders(app_: web.Application) -> None:
            app_[_REMINDER_TASK] = asyncio.get_running_loop().create_task(reminder_ticks())

        async def stop_reminders(app_: web.Application) -> None:
            task = app_.get(_REMINDER_TASK)
            if task is not None:
                task.cancel()

        app.on_startup.append(start_reminders)
        app.on_cleanup.append(stop_reminders)

    # The clock's timers. Unlike a reminder, a timer is said with a card
    # up: she asked for it seconds or minutes ago, and the egg does not
    # wait for a forgotten song choice. A call or a turn in progress
    # still holds it back (`_TIMER_SAY_WITHIN`).
    timers_to_say: list = []

    def check_clock() -> list:
        """One tick: lets the clock mark what ran out (the face draws
        it), and says the finished timers if she is free. Returns the
        timers said."""
        if clock is None:
            return []
        from saathi.tools.clock import done_line

        now = clock.now()
        timers_to_say.extend(clock.finished())
        timers_to_say[:] = [t for t in timers_to_say if now - t.ends_at < _TIMER_SAY_WITHIN]
        if not timers_to_say or not free_to_speak_first():
            return []
        if not core.handle(Event("initiative")):
            return []
        said = list(timers_to_say)
        timers_to_say.clear()
        line = done_line(said)
        path = getattr(store, "path", None)
        if path is not None:
            from saathi.identity.store import IdentityStore
            from saathi.initiative.policy import PolicyContext, evaluate
            from saathi.initiative.scheduler import InitiativeCandidate

            # Logged in the reminder lane: every unprompted utterance
            # records why (SPEC). Her own timer is never withheld.
            with IdentityStore(path) as own:
                evaluate(
                    own,
                    [InitiativeCandidate(kind="scheduled", reason=f"Timer done: {line}")],
                    PolicyContext(presence=True),
                    now=datetime.now(timezone.utc),
                )
        logger.info("timer said: %r", line)
        say_first(line, _TIMER_NOTE)
        return said

    app[CHECK_CLOCK] = check_clock

    if clock is not None and clock_tick_seconds:

        async def clock_ticks() -> None:
            while True:
                await asyncio.sleep(clock_tick_seconds)
                try:
                    check_clock()
                except Exception:
                    logger.exception("clock tick failed")

        async def start_clock(app_: web.Application) -> None:
            app_[_CLOCK_TASK] = asyncio.get_running_loop().create_task(clock_ticks())

        async def stop_clock(app_: web.Application) -> None:
            task = app_.get(_CLOCK_TASK)
            if task is not None:
                task.cancel()

        app.on_startup.append(start_clock)
        app.on_cleanup.append(stop_clock)

    # -- The family inbox (cloud/inbox.py) -------------------------------
    # A message from her family is said like a reminder: only when she
    # is free and no card is in the way, `core` taking "initiative",
    # with why on record. The poll is the network, so it runs on the
    # executor; speaking happens back here on the loop. One message per
    # tick, oldest first, so her answer belongs to exactly one: her next
    # words in the conversation that follows (the window after speaking
    # first, or talking over it) go to whoever sent it. The conversation
    # closing (IDLE or SLEEPING) ends that. It counts as said once the
    # state leaves SPEAKING -- finished, or interrupted by her. Like a
    # reminder's "fired", that is also what a failed text-to-speech
    # looks like (say_first logs it and carries on); lost: a "said"
    # signal out of say_first, which the reminder and clock lanes share.
    inbox_reply: dict[str, object] = {"message": None, "said": False}
    inbox_failing = {"value": False}

    def _in_background(what: str, fn, *args) -> None:
        future = asyncio.get_running_loop().run_in_executor(None, fn, *args)

        def finished(done) -> None:
            if not done.cancelled() and done.exception() is not None:
                logger.error("%s failed", what, exc_info=done.exception())

        future.add_done_callback(finished)

    def follow_message(state, _event: Event) -> None:
        message = inbox_reply["message"]
        if message is None:
            return
        if not inbox_reply["said"] and state != State.SPEAKING:
            inbox_reply["said"] = True
            _in_background(
                "marking a message spoken",
                inbox.mark_spoken,
                message.id,
                datetime.now(timezone.utc),
            )
        if state in (State.IDLE, State.SLEEPING):
            inbox_reply["message"] = None

    def reply_heard(heard: str) -> None:
        message = inbox_reply["message"]
        if message is None or not inbox_reply["said"]:
            return
        inbox_reply["message"] = None
        logger.info("her reply to message %s: %r", message.id, heard)
        _in_background(
            "sending her reply", inbox.record_reply, message.id, heard, datetime.now(timezone.utc)
        )

    async def check_inbox(now: datetime | None = None) -> list:
        """One tick: poll (reminders in, events out, messages to say),
        then say the oldest message if she is free. Returns what was
        said."""
        if inbox is None:
            return []
        loop = asyncio.get_running_loop()
        messages = await loop.run_in_executor(None, inbox.check, now)
        if not messages or inbox_reply["message"] is not None:
            return []
        if not free_to_speak_first() or _card_in_the_way():
            return []
        message = messages[0]
        path = getattr(store, "path", None)
        if path is not None:
            from saathi.identity.store import IdentityStore
            from saathi.initiative.policy import PolicyContext, evaluate
            from saathi.initiative.scheduler import InitiativeCandidate

            # Logged before `core` takes the initiative, as the reminder
            # lane does: a failure here must not leave the face SPEAKING
            # with nothing said. What the policy holds back stays unsaid.
            reason = f"Message from {message.sender or 'family'} [message_id={message.id}]"
            with IdentityStore(path) as own:
                rows = evaluate(
                    own,
                    [InitiativeCandidate(kind="scheduled", reason=reason)],
                    PolicyContext(presence=True),
                    now=now or datetime.now(timezone.utc),
                )
            if any(row["suppressed_by"] is not None for row in rows):
                return []
        if not core.handle(Event("initiative")):
            return []
        inbox_reply["message"], inbox_reply["said"] = message, False
        logger.info("message said: %r", message.line)
        say_first(message.line, _MESSAGE_NOTE)
        return [message]

    app[CHECK_INBOX] = check_inbox

    if inbox is not None:
        core.subscribe(follow_message)

    if inbox is not None and inbox_tick_seconds:

        async def inbox_ticks() -> None:
            while True:
                await asyncio.sleep(inbox_tick_seconds)
                try:
                    await check_inbox()
                    inbox_failing["value"] = False
                except Exception:
                    # Offline is normal: the first failure in a row gets
                    # the traceback, the rest a line each.
                    if not inbox_failing["value"]:
                        logger.exception("inbox tick failed")
                    else:
                        logger.warning("inbox tick failed again")
                    inbox_failing["value"] = True

        async def start_inbox(app_: web.Application) -> None:
            app_[_INBOX_TASK] = asyncio.get_running_loop().create_task(inbox_ticks())

        async def stop_inbox(app_: web.Application) -> None:
            task = app_.get(_INBOX_TASK)
            if task is not None:
                task.cancel()

        app.on_startup.append(start_inbox)
        app.on_cleanup.append(stop_inbox)

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
        ws = web.WebSocketResponse(heartbeat=_HEARTBEAT_SECONDS)
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
        if clock is not None:
            # Timers keep running across a reload; the page only counts.
            await ws.send_str(json.dumps(clock.message()))
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
                    if key not in (
                        LANGUAGE_KEY, TTS_BACKEND_KEY, CAPTIONS_KEY, WAKE_WORD_KEY
                    ) or not isinstance(value, str):
                        logger.warning("dropped malformed set_preference: %r", payload)
                        continue
                    if key in (CAPTIONS_KEY, WAKE_WORD_KEY) and value not in ("on", "off"):
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
                    refresh_wake()
                    continue
                if payload.get("type") == "media_event":
                    # The player reporting back ("ended", "error"). A
                    # report, not a decision: the controller updates
                    # what "play that again" means; nothing here does.
                    event = payload.get("event")
                    video_id = payload.get("video_id")
                    code = payload.get("code")
                    # Which player is reporting ("embed" or "browser");
                    # absent from the face page's frames, which predate
                    # the field. The controller reads it, not this.
                    target = payload.get("target")
                    if event == "error":
                        # Never silent: the code is the player's own
                        # (150/101 embedding disabled, 100 not found,
                        # "no_ready"/"api" from the panel's timeouts,
                        # "browser"/"wall" from the watch-page client).
                        logger.warning("player error for %s: code %s", video_id, code)
                    if media is not None and isinstance(event, str):
                        media.on_browser_event(
                            event,
                            video_id if isinstance(video_id, str) else None,
                            code=code if isinstance(code, (str, int)) else None,
                            target=target if isinstance(target, str) else None,
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
                if payload.get("type") == "clock_event":
                    # The clock's own buttons: close it, or stop a timer
                    # that has run out. Taps, like a card's.
                    if clock is not None:
                        if payload.get("action") == "hide":
                            clock.hide()
                        elif payload.get("action") == "dismiss" and isinstance(
                            payload.get("id"), str
                        ):
                            clock.dismiss(payload["id"])
                    continue
                if payload.get("type") == "phone_status":
                    can_call = payload.get("can_call")
                    via = payload.get("via")
                    if (
                        phone is not None
                        and isinstance(can_call, bool)
                        and (via is None or isinstance(via, str))
                    ):
                        phone.on_status(ws, can_call, via)
                    continue
                if payload.get("type") == "phone_event":
                    # The face page's Twilio call reporting (web_phone.py).
                    if phone is not None and hasattr(phone, "on_event"):
                        phone.on_event(payload)
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
                if payload.get("type") == "subscribe":
                    if payload.get("wake") is True:
                        wake_subscribers.add(ws)
                        await ws.send_str(json.dumps({"type": "wake", "listen": wake_allowed()}))
                    continue
                if payload.get("type") != "input":
                    continue
                kind = payload.get("event")
                if kind in ("press", "release"):
                    handle_input(kind, ws, by_name=payload.get("by") == "name")
        finally:
            websockets.discard(ws)
            wake_subscribers.discard(ws)
            if phone is not None:
                phone.on_status(ws, False)
            if press_owner["ws"] is ws and ws.close_code != WSCloseCode.OK:
                # Its press is still down and nothing will ever release
                # it: the Wi-Fi hiccup that took the socket took the
                # release with it (the shell drops a frame it cannot
                # send, and sends it once). A vanished client is
                # released for, as a touch cancel is a release on the
                # shell -- or core.py would sit in LISTENING, capture
                # open, until her next full hold, whose press would be a
                # no-op and whose words would be lost. Vanished: the
                # heartbeat's 1006, a transport error, a page going away
                # (1001). A client that closed in order (1000) had its
                # chance to release and is taken at its word -- the shell
                # always releases before it disconnects, and a test that
                # closes its socket mid-press expects the press to stand.
                logger.warning("client left with its press down; releasing it")
                handle_input("release", ws)
        return ws

    async def audio_handler(request: web.Request) -> web.WebSocketResponse:
        """The /audio path -- see the module docstring's inventory. The
        socket is handed to `remote_audio` whole: this handler only
        parses frames and routes them; what a frame *means* for the turn
        (forwarded or dropped, the ack of which play) is decided there."""
        ws = web.WebSocketResponse(heartbeat=_HEARTBEAT_SECONDS)
        await ws.prepare(request)
        replaced = remote_audio.attach(ws, asyncio.get_running_loop())
        if replaced is not None and not replaced.closed:
            # Its handler's loop ends on the close and its detach then
            # finds it is no longer the client, so nothing is undone.
            asyncio.ensure_future(replaced.close())
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    remote_audio.feed(msg.data)
                    continue
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning("dropped malformed audio message: %r", msg.data)
                    continue
                kind = payload.get("type")
                if kind == "hello":
                    rate = payload.get("sample_rate")
                    logger.info("audio client: %s at %s Hz", payload.get("client"), rate)
                    if rate != 16000:
                        # The contract says 16 kHz; the session assumes it.
                        # Said once, loudly, rather than transcribing noise.
                        logger.warning("audio client sample_rate %r is not 16000", rate)
                elif kind == "played":
                    play_id = payload.get("id")
                    if isinstance(play_id, str):
                        remote_audio.on_played(play_id)
                elif kind == "synthesized":
                    request_id = payload.get("id")
                    error = payload.get("error")
                    if isinstance(request_id, str):
                        remote_audio.on_synthesized(
                            request_id, None if error is None else str(error)
                        )
                else:
                    logger.warning("dropped unknown audio message: %r", payload)
        finally:
            remote_audio.detach(ws)
        return ws

    app.router.add_get("/", index)
    if store is not None:
        # The developer console (screen/admin.py): loopback only.
        from saathi.screen.admin import Console, add_routes

        add_routes(
            app,
            Console(
                store.path,
                session=session,
                info=admin_info,
                settings_changed=lambda: (_send_all(_settings_message(store)), refresh_wake()),
                cloud=inbox,
            ),
        )
    app.router.add_get("/ws", websocket_handler)
    if phone is not None and hasattr(phone, "token"):

        async def phone_token(request: web.Request) -> web.Response:
            # Loopback only (module docstring): the token places calls.
            if request.remote not in ("127.0.0.1", "::1"):
                raise web.HTTPForbidden()
            token = await asyncio.get_running_loop().run_in_executor(None, phone.token)
            return web.json_response({"token": token}, headers={"Cache-Control": "no-store"})

        app.router.add_get("/phone/token", phone_token)
    if family is not None:
        # Ctrl+P's pairing QR: loopback-only, never on the tunnelled app.
        family.install_local_routes(app)
    if remote_audio is not None:
        app.router.add_get("/audio", audio_handler)
    app.router.add_static("/static/", _STATIC_DIR)
    app.on_response_prepare.append(_never_cached)
    return app


async def _never_cached(request: web.Request, response: web.StreamResponse) -> None:
    """Every page and script of ours goes out `no-store`.

    Why: inside the APK every file's mtime is 1980 (Chaquopy unpacks with
    a fixed timestamp), so `Last-Modified` said "unchanged for decades" and
    the tablet's WebView cached the face heuristically for years: after an
    update it ran the old `roboeyes.js` and `main.js` (found 2026-10-29:
    every state threw and Twilio never started). Revalidation alone
    (`no-cache`) was the option that lost: the ETag aiohttp derives from
    that frozen mtime and the size misses an edit that keeps the size.
    Everything is served from this machine, so not caching costs nothing.
    """
    if request.path.startswith(("/static/", "/admin")) or request.path == "/":
        response.headers["Cache-Control"] = "no-store"


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
    phone=None,
    admin_info=None,
    follow_up_seconds: float | None = None,
    open_after_reply: bool = True,
    remote_audio=None,
    cloud=None,
    clock=None,
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
            phone=phone,
            admin_info=admin_info,
            follow_up_seconds=follow_up_seconds,
            open_after_reply=open_after_reply,
            remote_audio=remote_audio,
            cloud=cloud,
            clock=clock,
        ),
        host=host,
        port=port,
        print=None,
    )
