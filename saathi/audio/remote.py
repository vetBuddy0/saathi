"""The `/audio` seam: a phone or tablet as the microphone and the speaker
while the engine stays on the Pi or the laptop.

Exists because the engine is not moving into the APK yet. Speech-to-text,
the model, TTS, memory and the state machine all stay where they are
today; the Android shell is a face WebView, a mic and a speaker on the
same Wi-Fi, speaking the protocol in `screen/server.py`'s docstring.
That split is the point, not a stopgap: everything the shell knows how
to do -- hold to talk and stream PCM, play one WAV and say "played",
stop on "stop" -- is exactly what it would still do if the engine ran
on the phone itself, so the engine can move later without the shell
changing. This class is the engine's side of that line. `Capture` and
`playback.play` are what it replaces while a client is attached, and
what it hands back to the moment one isn't: `player()` falls back to
local `play` when nothing is connected, so `cascade.py` never knows
which it got.

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
            if replaced is not None and replaced is not ws:
                current, self._current = self._current, None
        if replaced is not None and replaced is not ws:
            logger.info("audio client replaced by a new connection")
        if current is not None:
            logger.warning("audio client replaced mid-sentence; releasing %s", current.id)
            current._release()
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
        if current is not None:
            logger.warning("audio client left mid-sentence; releasing %s", current.id)
            current._release()

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

    def feed(self, pcm: bytes) -> None:
        """One binary frame from the client. Forwarded only between
        `start_listening()` and `stop_listening()`: the client may send
        whenever it likes; the server decides what counts as her turn."""
        with self._lock:
            sink = self._sink
        if sink is not None:
            sink(pcm)

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
