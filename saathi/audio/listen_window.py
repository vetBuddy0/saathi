"""The open mic after Saathi speaks: listening for a follow-up with no
name and no key.

Why it exists: SPEC.md says "once a conversation is open, follow-ups
need no trigger -- AEC is what makes an open mic safe", and nothing did
it. Live (2026-10-08) every reply ended at IDLE, so "and what about
tomorrow?" said straight after an answer was ignored unless she said
"Saathi" again first. A person you are talking to doesn't need their
name before every sentence.

What it decides, on the mic thread, from audio alone: whether she
*started* talking within the window (`window_seconds`), and once she
has, when she finished (the same `Endpointer` a wake-word turn ends on).
Nothing leaves the device until she starts -- the window's audio is
only held locally as a short preroll, so a quiet window costs no cloud
call at all.

The guard: the first `guard_seconds` after the reply are thrown away
unheard. The echo canceller removes most of her own voice, but the very
tail of the last word (speaker buffer, room reverb) can arrive after
`say()` returns, and a window that opened on Saathi's own "...today."
would answer itself. 0.35 s is longer than the tail measured on the
laptop speaker and shorter than anyone starts a reply.

Onset needs a few speech chunks in a row (`onset_chunks`, ~100 ms), not
one: a single 32 ms VAD hit is a cough or a cup set down, and starting a
turn on it would mean a cloud call and a THINKING face for nothing. The
turn's own speech gate (cascade.py) still has the last word.

Lost: handing the window to the wake listener (it already holds a mic).
The listener only listens while the device is idle and checks for the
name; the window wants every word, not the name, and keeping the two
apart means a follow-up never has to pass the name check.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from saathi.audio.vad import CHUNK_BYTES, SAMPLE_RATE
from saathi.audio.wake import Endpointer

_BYTES_PER_SECOND = SAMPLE_RATE * 2

FOLLOW_UP_SECONDS = 7.0
GUARD_SECONDS = 0.35


class ListenWindow:
    """Audio in (any sizes), events out. `push()` returns:

    - before she starts: None while waiting, "start" the moment she
      starts (then `started_audio()` is everything of hers so far,
      preroll included), or "lapse" when the window passed in silence;
    - after "start": None, then "end" once the endpointer says she has
      finished. Everything pushed after "start" is hers, unfiltered.
    """

    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        window_seconds: float = FOLLOW_UP_SECONDS,
        guard_seconds: float = GUARD_SECONDS,
        onset_chunks: int = 3,
        preroll_seconds: float = 0.3,
        endpointer: Endpointer | None = None,
    ) -> None:
        self._is_speech = is_speech
        self._guard_bytes = round(guard_seconds * _BYTES_PER_SECOND)
        self._window_bytes = round(window_seconds * _BYTES_PER_SECOND)
        self._onset_chunks = max(1, onset_chunks)
        chunk_seconds = CHUNK_BYTES / _BYTES_PER_SECOND
        self._preroll: deque[bytes] = deque(maxlen=max(1, round(preroll_seconds / chunk_seconds)))
        self._endpointer = endpointer or Endpointer(is_speech)
        self._seen = 0  # bytes pushed so far
        self._buffer = b""
        self._run = 0  # speech chunks in a row
        self._started: bytes | None = None
        self.lapsed = False
        self.ended = False

    @property
    def started(self) -> bool:
        return self._started is not None

    @property
    def last_speech_at(self) -> float | None:
        return self._endpointer.last_speech_at

    def started_audio(self) -> bytes:
        return self._started or b""

    def push(self, audio: bytes) -> str | None:
        if self.lapsed or self.ended:
            return None
        if self._started is not None:
            if self._endpointer.push(audio):
                self.ended = True
                return "end"
            return None
        before = self._seen
        self._seen += len(audio)
        if self._seen <= self._guard_bytes:
            return None
        if before < self._guard_bytes:
            audio = audio[self._guard_bytes - before :]  # the guard's share is dropped unheard
        self._buffer += audio
        while len(self._buffer) >= CHUNK_BYTES:
            chunk, self._buffer = self._buffer[:CHUNK_BYTES], self._buffer[CHUNK_BYTES:]
            self._preroll.append(chunk)
            self._run = self._run + 1 if self._is_speech(chunk) else 0
            if self._run >= self._onset_chunks:
                self._started = b"".join(self._preroll) + self._buffer
                self._buffer = b""
                if self._endpointer.push(self._started):
                    self.ended = True
                return "start"
        if self._seen >= self._window_bytes:
            self.lapsed = True
            return "lapse"
        return None
