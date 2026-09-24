"""Voice activity detection — answers "is she talking right now."

Barge-in (checkpoint 2) is the reason this exists before a full voice
engine does: `core.py` needs to know the instant she starts talking over
Saathi, not just "has she stopped" between turns. Wraps `pysilero-vad`
(bundled ggml model, no network at runtime — checked before `torch`-based
`silero-vad`: that package pulls torch+torchaudio unconditionally, and
`pysilero-vad` ships real `manylinux aarch64` wheels, which SPEC.md's "a
missing ARM wheel must fail a build" made the deciding factor).

Fixed input shape: 16kHz mono 16-bit PCM, 512 samples (32 ms) per chunk —
`audio/capture.py` is what guarantees frames arrive in exactly that shape,
so nothing here resamples or buffers partial chunks.
"""

from __future__ import annotations

from pysilero_vad import SileroVoiceActivityDetector

SAMPLE_RATE = 16000
CHUNK_SAMPLES = SileroVoiceActivityDetector.chunk_samples()
CHUNK_BYTES = SileroVoiceActivityDetector.chunk_bytes()


class VoiceActivityDetector:
    """One chunk in, one speech probability out. No state beyond the
    model's own internal recurrent state — `reset()` clears that."""

    def __init__(self, threshold: float = 0.5) -> None:
        self._model = SileroVoiceActivityDetector()
        self.threshold = threshold

    def probability(self, chunk: bytes) -> float:
        if len(chunk) != CHUNK_BYTES:
            raise ValueError(f"expected {CHUNK_BYTES}-byte chunks, got {len(chunk)}")
        return self._model.process_chunk(chunk)

    def is_speech(self, chunk: bytes) -> bool:
        return self.probability(chunk) >= self.threshold

    def reset(self) -> None:
        self._model.reset()


class SpeechStartDetector:
    """Debounced speech-onset detector: fires once, on the chunk where
    `consecutive_chunks` in a row have crossed the threshold — not on the
    first noisy chunk. At 32 ms/chunk, the default (3) is ~96 ms of
    sustained speech before it declares "she's talking," leaving headroom
    inside the ~300 ms barge-in budget for the stop itself.
    """

    def __init__(self, vad: VoiceActivityDetector, consecutive_chunks: int = 3) -> None:
        self._vad = vad
        self._consecutive_chunks = consecutive_chunks
        self._run_length = 0
        self._fired = False

    def push(self, chunk: bytes) -> bool:
        """Feed one chunk. Returns True on the single chunk where onset
        is declared; False every other time, including every chunk after
        that until `reset()`."""
        if self._vad.is_speech(chunk):
            self._run_length += 1
        else:
            self._run_length = 0

        if not self._fired and self._run_length >= self._consecutive_chunks:
            self._fired = True
            return True
        return False

    def reset(self) -> None:
        self._run_length = 0
        self._fired = False
        self._vad.reset()


def contains_speech(pcm: bytes, *, consecutive_chunks: int = 3) -> bool:
    """Whole-buffer question: did she say anything at all in this turn?

    Exists because Whisper does not answer it. Measured on this machine
    (2026-09-24, four consecutive probes of a silent echo-cancelled
    source, RMS 1-750): `whisper-large-v3-turbo` returned
    `no_speech_prob=0.0000` every time while transcribing the silence as
    " Thank you.", " I'm going to go." and " voice. That is me. Thank
    you." — so a `no_speech_prob` threshold, the obvious guard, cannot
    tell a silent turn from a spoken one on this path. Silero can: the
    same buffers scored 0 of 119 chunks over threshold (max 0.37).

    Reuses `SpeechStartDetector`'s debounce rather than a fresh
    threshold, so "speech" here means the same ~96 ms of sustained
    voice that barge-in already trusts — one definition, not two.
    A trailing partial chunk is dropped, not zero-padded: it is under
    32 ms and can't change the answer.
    """
    detector = SpeechStartDetector(VoiceActivityDetector(), consecutive_chunks)
    for offset in range(0, len(pcm) - CHUNK_BYTES + 1, CHUNK_BYTES):
        if detector.push(pcm[offset : offset + CHUNK_BYTES]):
            return True
    return False
