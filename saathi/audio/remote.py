"""The `/audio` seam: a phone or tablet as the microphone and the speaker,
whether the engine is on the Pi, on the laptop, or inside the phone.

Exists so the engine never knows where its microphone and speaker are.
It began (2026-10-08, morning) as the way to use a phone as a mic and a
speaker while speech-to-text, the model, TTS, memory and the state
machine stayed on the Pi or the laptop; the Android shell is a face
WebView, a mic and a speaker speaking the protocol in
`screen/server.py`'s docstring. The split was the point, not a stopgap:
everything the shell knows how to do -- hold to talk and stream PCM,
play one WAV and say "played", stop on "stop" -- is exactly what it
still does now that the engine also runs inside the APK (Chaquopy, at
`127.0.0.1:8765`, `SAATHI_AUDIO=remote` in `cli.py`): the shell connects
to localhost instead of a Wi-Fi address and nothing else about it
changes. This class is the engine's side of that line. `Capture` and
`playback.play` are what it replaces while a client is attached, and
what it hands back to the moment one isn't: `player()` falls back to
`fallback` (local `play` by default) when nothing is connected, so
`cascade.py` never knows which it got.

The seam also runs backwards since the engine moved onto the phone:
`synthesize()` asks the attached client to render one sentence with
the phone's own text-to-speech engine and hands the WAV back
(`voice/tts/remote_backend.py` is the `TTSBackend` over it). Why ask
the shell rather than synthesize on the phone from Python: `piper-tts`
sits on `onnxruntime`, which has no Chaquopy wheel, and the Google
voices need credentials and a network, so without this the phone had no
voice of last resort at all. Why over the socket and not through
Chaquopy's Java bridge from inside the TTS backend: the engine must not
know it is on a phone -- a backend reaching into Android's
`TextToSpeech` would have been the voice engine touching the shell,
the thing the five interfaces exist to stop; through the socket the
same engine runs unchanged on a laptop with the phone attached over
Wi-Fi, and the test suite can play the phone. The frames: engine ->
client text `{"type": "synthesize", "id": ..., "text": ..., "language":
...}` (`language` is `voice/language.py`'s key, the shell maps it to a
locale), client -> engine text `{"type": "synthesized", "id": ...}`
followed at once by one binary frame holding the WAV -- or `{"type":
"synthesized", "id": ..., "error": "..."}` with nothing after it when
the phone could not render it. The text frame tags the binary one that
follows: between a `synthesized` and its WAV the client sends nothing
else, so a mic frame can never be taken for a sentence. The wait is
bounded by a timeout, and the client leaving releases it at once,
exactly as for a play.

What lost: streaming TTS to the client as PCM chunks, the way the mic
comes in. Rejected for now because one WAV per sentence is what the
cascade already produces (`voice/tts/__init__.py`'s `synthesize_stream`
yields whole sentences, and `_speak()` plays them one at a time); a
chunked playback protocol would have meant a jitter buffer on the phone
and a second "where are we in the sentence" clock on the engine for
barge-in, for no gain in time-to-first-audio that the sentence split
hasn't already bought. A client is a dumb speaker: play this file to
the end, then say so.

One client at a time; a new connection replaces the old one. Threads:
`attach`/`detach`/`feed`/`on_played`/`start_listening`/`stop_listening`
are called on the event loop thread by the server; `player()` and the
handle it returns are called from the cascade's `say()` thread, and
`stop()` from whichever thread `interrupt()` runs on (the loop, in
`screen/server.py`). Every send hops onto the loop through
`call_soon_threadsafe` and joins one ordered chain per client, so a
`stop` can never overtake the frames of the play it is stopping. A
`wait()` is bounded: the WAV's own duration plus a grace period, after
which the engine moves on without the ack -- a dead phone must not
leave her face stuck on "speaking". The client leaving, or being
replaced by a new connection, releases the wait at once rather than
at the bound. A client that dies without a close frame is the server's
to notice: `screen/server.py` opens `/audio` with a heartbeat, so the
socket closes (and `detach` runs) a few seconds after the phone goes
quiet instead of whenever the kernel gives up on the TCP connection.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import threading
import wave
from pathlib import Path
from typing import Any, Callable

from saathi.audio.playback import PlaybackHandle, play

logger = logging.getLogger(__name__)

# How long past the WAV's own length a play may wait for the client's
# "played" before the engine gives up on it. Generous enough for the
# phone's audio pipeline to start and for Wi-Fi to deliver a 200 KB
# frame; short enough that a dropped client costs one sentence, not a
# stuck state machine.
DEFAULT_GRACE_SECONDS = 3.0

# How long `synthesize()` waits for the phone to render one sentence
# before giving up on it. A phone's text-to-speech engine takes a second
# or two to start the first time and well under a second per sentence
# after that; a client that is gone is detached by the server's
# heartbeat within seconds and releases the wait early, so this bound
# only ever decides how long a *live but stuck* phone can hold one
# sentence.
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 10.0

# If a WAV can't be parsed (it always can -- every backend in voice/tts
# writes through `wave` -- but a seam must not crash on its input),
# estimate from size as 16 kHz mono 16-bit: a lower bound on the sample
# rate any backend here uses, so an overestimate of the duration, never
# a wait that is too short.
_FALLBACK_BYTES_PER_SECOND = 32000


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            rate = wav_file.getframerate()
            if rate <= 0:
                raise ValueError("no sample rate")
            return wav_file.getnframes() / rate
    except Exception:
        return len(data) / _FALLBACK_BYTES_PER_SECOND


class RemotePlayback:
    """What `RemoteAudio.player()` returns: the same three things
    `playback.PlaybackHandle` offers, so `cascade.py`'s `_speak()` and
    `interrupt()` treat the two alike."""

    def __init__(self, owner: RemoteAudio, play_id: str, timeout_seconds: float) -> None:
        self._owner = owner
        self.id = play_id
        self.timeout_seconds = timeout_seconds
        self._done = threading.Event()
        # True once the client said "played" (not on stop, not on timeout).
        self.acknowledged = False

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    def wait(self) -> None:
        if not self._done.wait(self.timeout_seconds):
            logger.warning(
                "no 'played' for %s within %.1fs; moving on without it",
                self.id,
                self.timeout_seconds,
            )
            self._owner._forget(self)

    def stop(self) -> None:
        self._owner._stop(self)

    def _release(self) -> None:
        self._done.set()


class RemoteSynthesisError(RuntimeError):
    """`synthesize()` got no WAV: no client attached, the client left or
    was replaced mid-request, it answered with an error, or the timeout
    passed. One class: the backend over this seam treats them all the
    same way (a short silence, a warning), and `str(exc)` says which."""


class _SynthesisRequest:
    """One sentence asked of the client: its id, and the WAV or the
    error that answers it. `wait()` is the asking thread's; `complete()`
    and `fail()` are called by the loop thread."""

    def __init__(self, request_id: str) -> None:
        self.id = request_id
        self.wav: bytes | None = None
        self.error: str | None = None
        self._done = threading.Event()

    def complete(self, wav: bytes) -> None:
        self.wav = wav
        self._done.set()

    def fail(self, error: str) -> None:
        self.error = error
        self._done.set()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


class RemoteAudio:
    def __init__(
        self,
        fallback: Callable[[str, Path], PlaybackHandle] = play,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
    ) -> None:
        self._fallback = fallback
        self._grace_seconds = grace_seconds
        self._lock = threading.Lock()
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink: Callable[[bytes], None] | None = None
        self._current: RemotePlayback | None = None
        self._ids = itertools.count(1)
        # The per-client ordered send chain; loop thread only.
        self._send_chain: asyncio.Future | None = None
        # Sentences asked of the client and not yet answered, by id;
        # and the one whose WAV is the next binary frame (its
        # `synthesized` has arrived, the WAV has not).
        self._synth_requests: dict[str, _SynthesisRequest] = {}
        self._synth_pending: _SynthesisRequest | None = None
        self._synth_ids = itertools.count(1)

    # -- the client (server side, loop thread) ------------------------------

    def attach(self, ws: Any, loop: asyncio.AbstractEventLoop) -> Any:
        """Make `ws` the one client. Returns the client it replaced (for
        the server to close), or None. A play waiting on the replaced
        client is released here, exactly as `detach` releases it: the
        old handler's own close finds it is no longer the client and
        returns early, so nothing else would. The sentence is lost, not
        re-sent -- the new client never saw its header, and a reconnect
        mid-sentence (the app resumed, a Wi-Fi blip) should cost that
        sentence, not the WAV's length plus the grace with her face held
        on "speaking"."""
        with self._lock:
            replaced = self._ws
            self._ws = ws
            self._loop = loop
            self._send_chain = None
            current = None
            asked: list[_SynthesisRequest] = []
            if replaced is not None and replaced is not ws:
                current, self._current = self._current, None
                asked = self._take_synthesis_requests()
        if replaced is not None and replaced is not ws:
            logger.info("audio client replaced by a new connection")
        if current is not None:
            logger.warning("audio client replaced mid-sentence; releasing %s", current.id)
            current._release()
        for request in asked:
            request.fail("audio client replaced before it answered")
        return replaced if replaced is not ws else None

    def detach(self, ws: Any) -> None:
        """Forget `ws` -- only if it is still the client; the close of a
        connection that was already replaced changes nothing. A play
        waiting on this client is released: its sentence is lost, the
        turn is not."""
        with self._lock:
            if self._ws is not ws:
                return
            self._ws = None
            self._loop = None
            self._send_chain = None
            current, self._current = self._current, None
            asked = self._take_synthesis_requests()
        if current is not None:
            logger.warning("audio client left mid-sentence; releasing %s", current.id)
            current._release()
        for request in asked:
            request.fail("audio client left before it answered")

    def _take_synthesis_requests(self) -> list[_SynthesisRequest]:
        """Every unanswered `synthesize()`, cleared; lock held by the
        caller. Failed outside the lock, by the caller."""
        asked = list(self._synth_requests.values())
        self._synth_requests = {}
        if self._synth_pending is not None:
            asked.append(self._synth_pending)
            self._synth_pending = None
        return asked

    @property
    def attached(self) -> bool:
        ws = self._ws
        return ws is not None and not ws.closed

    # -- the microphone --------------------------------------------------

    def start_listening(self, sink: Callable[[bytes], None]) -> None:
        with self._lock:
            self._sink = sink

    def stop_listening(self) -> None:
        with self._lock:
            self._sink = None

    def feed(self, data: bytes) -> None:
        """One binary frame from the client. The frame right after a
        `synthesized` is that sentence's WAV and goes to whoever asked
        for it; every other frame is microphone audio, forwarded only
        between `start_listening()` and `stop_listening()`: the client
        may send whenever it likes; the server decides what counts as
        her turn."""
        with self._lock:
            request, self._synth_pending = self._synth_pending, None
            sink = self._sink
        if request is not None:
            request.complete(data)
            return
        if sink is not None:
            sink(data)

    # -- the phone's own voice ------------------------------------------

    def synthesize(
        self,
        text: str,
        language: str,
        timeout: float = DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    ) -> bytes:
        """Ask the attached client to render `text` in `language` with
        the phone's text-to-speech and return the WAV. From the
        cascade's synthesis thread, like a backend's own synthesis; the
        reply arrives on the loop thread (`on_synthesized`, then
        `feed`). Raises `RemoteSynthesisError` -- never returns silence
        or None -- when there is no client, the client leaves or is
        replaced first, it answers with an error, or `timeout` passes;
        the backend over this decides what a lost sentence sounds like."""
        with self._lock:
            ws, loop = self._ws, self._loop
            if ws is None or loop is None or ws.closed:
                raise RemoteSynthesisError("no audio client is attached")
            request = _SynthesisRequest(f"tts-{next(self._synth_ids)}")
            self._synth_requests[request.id] = request
        frame = json.dumps(
            {"type": "synthesize", "id": request.id, "text": text, "language": language}
        )
        self._enqueue(ws, loop, [frame])
        if not request.wait(timeout):
            # Forgotten here; its answer, if it still comes, is eaten
            # by `on_synthesized`/`feed` rather than heard as mic audio.
            with self._lock:
                self._synth_requests.pop(request.id, None)
            raise RemoteSynthesisError(f"no WAV for {request.id} within {timeout:.1f}s")
        if request.error is not None:
            raise RemoteSynthesisError(request.error)
        assert request.wav is not None
        return request.wav

    def on_synthesized(self, request_id: str, error: str | None = None) -> None:
        """The client's answer to `synthesize()`: with `error`, the
        sentence could not be rendered and no WAV follows; without it,
        the next binary frame is the WAV. That holds for a stale id too
        -- a request already given up on (`synthesize()` timed out) --
        because the WAV still follows and must be eaten here, not heard
        as microphone audio; nobody waits for it, so it is dropped. A
        second `synthesized` arriving while the previous one's WAV is
        still owed fails the previous: the client broke the one rule of
        this exchange, and a mic frame must not be handed out as a
        sentence."""
        with self._lock:
            request = self._synth_requests.pop(request_id, None)
            previous = None
            if error is None:
                if request is None:
                    request = _SynthesisRequest(request_id)  # an orphan: eats its WAV
                previous, self._synth_pending = self._synth_pending, request
        if error is not None:
            if request is not None:
                request.fail(f"the client could not synthesize {request_id}: {error}")
            return
        if previous is not None:
            logger.warning("no WAV followed 'synthesized' for %s; failing it", previous.id)
            previous.fail("the client sent no WAV after its 'synthesized'")

    # -- the speaker -----------------------------------------------------

    def player(self, sink_id: str, wav_path: Path) -> Any:
        """`play()`'s signature, from `cascade.py`'s `say()` thread. With
        a client: one `play` header, one binary WAV, and a handle whose
        `wait()` returns on the client's `played` (or gives up after the
        WAV's length plus the grace). Without one: local playback, as if
        this class were not here."""
        with self._lock:
            ws, loop = self._ws, self._loop
            if ws is None or loop is None or ws.closed:
                ws = loop = None
        if ws is None or loop is None:
            return self._fallback(sink_id, wav_path)
        data = wav_path.read_bytes()
        play_id = f"play-{next(self._ids)}"
        handle = RemotePlayback(self, play_id, wav_seconds(data) + self._grace_seconds)
        with self._lock:
            if self._ws is not ws:
                # Detached between the check and here: local, as above.
                return self._fallback(sink_id, wav_path)
            previous, self._current = self._current, handle
        if previous is not None and not previous.finished:
            # Never leaves a thread blocked: a play nobody waited for
            # (say() moved on without wait(), which it doesn't do today).
            previous._release()
        header = json.dumps({"type": "play", "id": play_id, "format": "wav"})
        self._enqueue(ws, loop, [header, data])
        return handle

    def on_played(self, play_id: str) -> None:
        """The client finished (or stopped) the play with this id. A
        stale id -- the ack of a play already given up on, or of the one
        a `stop` cut short after the next began -- is ignored."""
        with self._lock:
            current = self._current
            if current is None or current.id != play_id:
                return
            self._current = None
        current.acknowledged = True
        current._release()

    def _stop(self, handle: RemotePlayback) -> None:
        if handle.finished:
            return
        with self._lock:
            ws, loop = self._ws, self._loop
            if self._current is handle:
                self._current = None
        if ws is not None and loop is not None and not ws.closed:
            self._enqueue(ws, loop, [json.dumps({"type": "stop"})])
        handle._release()

    def _forget(self, handle: RemotePlayback) -> None:
        with self._lock:
            if self._current is handle:
                self._current = None
        handle._release()

    def _enqueue(self, ws: Any, loop: asyncio.AbstractEventLoop, frames: list[str | bytes]) -> None:
        """Send `frames`, in order, after everything queued before them
        for this client. Callable from any thread; the chain itself is
        only ever touched on the loop."""

        def _queue() -> None:
            previous = self._send_chain

            async def _send() -> None:
                if previous is not None:
                    try:
                        await previous
                    except Exception:
                        pass
                try:
                    for frame in frames:
                        if ws.closed:
                            return
                        if isinstance(frame, bytes):
                            await ws.send_bytes(frame)
                        else:
                            await ws.send_str(frame)
                except Exception:
                    # The client dropped mid-send: the handler's close
                    # will detach it and release whatever was waiting.
                    logger.warning("sending to the audio client failed", exc_info=True)

            self._send_chain = loop.create_task(_send())

        loop.call_soon_threadsafe(_queue)
