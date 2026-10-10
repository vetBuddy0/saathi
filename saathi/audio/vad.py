"""Voice activity detection — answers "is she talking right now."

Barge-in (checkpoint 2) is the reason this exists before a full voice
engine does: `core.py` needs to know the instant she starts talking over
Saathi, not just "has she stopped" between turns. Wraps `pysilero-vad`
(bundled ggml model, no network at runtime — checked before `torch`-based
`silero-vad`: that package pulls torch+torchaudio unconditionally, and
`pysilero-vad` ships real `manylinux aarch64` wheels, which SPEC.md's "a
missing ARM wheel must fail a build" made the deciding factor).

Two gates behind one class. `pysilero-vad` is imported when the first
detector is built, not when this module is: on Android (Chaquopy, the
engine inside the APK) there is no wheel for it at all, and the same
engine has to run there. When the import fails the detector is an RMS
energy gate over the same 32 ms chunks, with the same debounce on top —
`SpeechStartDetector` and `contains_speech()` don't know which one they
got, and `VoiceActivityDetector.gate` says. The process logs which gate
is active, once. What lost for the fallback: `webrtcvad` (a C extension
with no Chaquopy wheel either — the same problem again), and a spectral
gate written here in numpy (more to get wrong, and nothing to measure it
against until a phone has been in a room; an RMS gate is at least
predictable). The energy gate is the degraded path, not a second
product: Linux with everything installed behaves exactly as before.

Fixed input shape: 16kHz mono 16-bit PCM, 512 samples (32 ms) per chunk —
`audio/capture.py` is what guarantees frames arrive in exactly that shape,
so nothing here resamples or buffers partial chunks. The constants are
written down rather than read off the Silero class because the import is
lazy now; `_silero_class()` checks the package still agrees the moment it
loads, so a chunk-size change upstream fails loudly instead of feeding
the model the wrong window.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# Silero's fixed window at 16 kHz. pysilero-vad's own
# `chunk_samples()`/`chunk_bytes()` return exactly these; see the module
# docstring for why they are not read from it.
CHUNK_SAMPLES = 512
CHUNK_BYTES = CHUNK_SAMPLES * 2

# The energy gate's threshold: per-chunk RMS, in int16 sample units after
# removing the chunk's DC offset (a biased mic must not read as constant
# speech). 1000 is about -30 dBFS. Why there: the echo-cancelled source's
# silence, measured on this machine (2026-09-24, see `contains_speech`'s
# docstring), sat at RMS 1-750, so 1000 clears it with margin; the
# `known_sentence.wav` fixture's spoken chunks have a median RMS of
# ~1650 and runs of 16+ consecutive chunks over 1000, against the
# three-chunk debounce `SpeechStartDetector` asks for. A single click
# or a door closing is one or two chunks, not three.
ENERGY_GATE_RMS = 1000

# Which gate has been logged for this process, so it's said once —
# `contains_speech()` builds a fresh detector every turn.
_announced_gate: str | None = None


def _silero_class():
    """The real detector's class, or None when `pysilero_vad` isn't
    importable (Android). Imported here, at first use, and never at
    module import — that is what lets the rest of the package load
    without it."""
    try:
        from pysilero_vad import SileroVoiceActivityDetector
    except ImportError:
        return None
    if SileroVoiceActivityDetector.chunk_bytes() != CHUNK_BYTES:
        raise RuntimeError(
            f"pysilero-vad wants {SileroVoiceActivityDetector.chunk_bytes()}-byte chunks; "
            f"this module and audio/capture.py are built for {CHUNK_BYTES}"
        )
    return SileroVoiceActivityDetector


def _announce(gate: str) -> None:
    global _announced_gate
    if gate == _announced_gate:
        return
    _announced_gate = gate
    if gate == "silero":
        logger.info("voice activity gate: Silero (pysilero-vad)")
    else:
        logger.info(
            "voice activity gate: RMS energy, threshold %d (pysilero-vad not importable)",
            ENERGY_GATE_RMS,
        )


class _EnergyGate:
    """Stands in for `SileroVoiceActivityDetector`: the same two methods,
    so `VoiceActivityDetector` is one class, not two. "Probability" is
    the chunk's RMS scaled so that `ENERGY_GATE_RMS` lands exactly on
    0.5 — the default threshold — and twice it saturates at 1.0."""

    def process_chunk(self, chunk: bytes) -> float:
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float64)
        samples -= samples.mean()
        rms = float(np.sqrt(np.mean(samples * samples)))
        return min(1.0, 0.5 * rms / ENERGY_GATE_RMS)

    def reset(self) -> None:
        pass  # no state to clear


class VoiceActivityDetector:
    """One chunk in, one speech probability out. No state beyond the
    model's own internal recurrent state — `reset()` clears that.
    `gate` is "silero" or "energy": which one this process got."""

    def __init__(self, threshold: float = 0.5) -> None:
        silero = _silero_class()
        if silero is not None:
            self._model = silero()
            self.gate = "silero"
        else:
            self._model = _EnergyGate()
            self.gate = "energy"
        _announce(self.gate)
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
    32 ms and can't change the answer. Whichever gate the detector
    resolved to (see the module docstring), this walks the buffer the
    same way.
    """
    detector = SpeechStartDetector(VoiceActivityDetector(), consecutive_chunks)
    for offset in range(0, len(pcm) - CHUNK_BYTES + 1, CHUNK_BYTES):
        if detector.push(pcm[offset : offset + CHUNK_BYTES]):
            return True
    return False
