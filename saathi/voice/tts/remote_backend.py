"""The phone's own voice as a `TTSBackend`: each sentence is rendered by
the Android text-to-speech engine of whatever client is attached on
`/audio`, through `audio/remote.py`'s `synthesize()`.

Exists as the voice of last resort for the engine running inside the
Android app. Piper -- the last resort everywhere else -- cannot go
there (`piper-tts` sits on `onnxruntime`, which has no Chaquopy
wheel), and the Google voices need a key file and a network. Without
this, a phone with no credentials, or no signal, had a face and a
microphone and nothing to answer with. Android's engine is on every
phone, offline, and free; it is not a warm voice, which is why
`cli.py` adds it beside the others rather than in front of them: the
stored preference (Chirp by default) still wins whenever it can, and
`cascade._current_backend()` reaches this one only when nothing she
would rather hear is available.

Why it goes through the socket and not Chaquopy's Java bridge: see
`audio/remote.py` -- a backend that called `android.speech.tts` from
Python would have had the voice engine reaching into the shell, and
would only exist on the phone; this one is the same class on a laptop
with a phone attached over Wi-Fi, and in the test suite with a fake.

A failed sentence -- no client, the client left, the phone could not
render it, the timeout -- is a short silence plus a warning, never a
raise: `cascade._prefetch_next_chunk()` only enqueues on success, so an
exception in the synthesis thread leaves `say()` waiting forever (the
same rule `google_backend._guarded()` records). `available()` is the
one place the truth is told: "no phone is attached" is an ordinary
unavailable state for the panel, not a failure.

What lost: a `preload()` that warms the phone's engine during
`end_turn()`'s network wait. The shell initialises its engine when it
connects, not on the first sentence, which is where that cost belongs;
nothing here can make it earlier.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Iterator

from saathi.audio.remote import (
    DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    RemoteAudio,
    RemoteSynthesisError,
)
from saathi.voice.tts import TTSBackend

logger = logging.getLogger(__name__)

REMOTE_BACKEND_ID = "android-tts"

# What a lost sentence sounds like: 100 ms of silence at the mic rate,
# the same length `google_backend._guarded()` substitutes, so the turn
# keeps its shape (one play per sentence, one `played` per play).
_SILENCE_SECONDS = 0.1
_SILENCE_RATE_HZ = 16000


def silent_wav(seconds: float = _SILENCE_SECONDS, rate_hz: int = _SILENCE_RATE_HZ) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate_hz)
        wav_file.writeframes(b"\x00\x00" * int(rate_hz * seconds))
    return buffer.getvalue()


class RemoteTTSBackend(TTSBackend):
    id = REMOTE_BACKEND_ID
    display_name = "Phone voice"
    license = "Platform (the phone's own text-to-speech engine; nothing redistributed)"
    local = True

    def __init__(
        self,
        remote: RemoteAudio,
        timeout_seconds: float = DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    ) -> None:
        self._remote = remote
        self._timeout_seconds = timeout_seconds

    def available(self) -> tuple[bool, str]:
        if self._remote.attached:
            return True, ""
        return False, "no phone is attached on /audio"

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        for sentence in sentences:
            try:
                yield self._remote.synthesize(sentence, language, self._timeout_seconds)
            except RemoteSynthesisError as exc:
                logger.warning(
                    "%s: synthesis failed for %r -- %s; a short silence instead",
                    self.id,
                    sentence,
                    exc,
                )
                yield silent_wav()

    def cost_per_million_chars_usd(self) -> float:
        return 0.0
