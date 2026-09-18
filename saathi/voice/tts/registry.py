"""The one place that lists every backend that exists, in the fixed
order the settings panel (item C/G) and the comparison report show them
in. Constructing a backend here must stay cheap and side-effect-free --
see `TTSBackend`'s docstring -- `available()` is what actually decides
whether each one can be used, and it's checked lazily by callers, not
here.
"""

from __future__ import annotations

from saathi.voice.tts import TTSBackend
from saathi.voice.tts.google_backend import GoogleChirp3HDBackend, GoogleNeural2WaveNetBackend
from saathi.voice.tts.kokoro_backend import KokoroBackend
from saathi.voice.tts.melo_backend import MeloTTSBackend
from saathi.voice.tts.piper_backend import PiperBackend

# Piper first and always present -- the last-resort offline fallback
# `cascade.py` falls back to if the preferred backend is unavailable.
DEFAULT_BACKEND_ID = "piper"


def default_backends() -> dict[str, TTSBackend]:
    backends: list[TTSBackend] = [
        PiperBackend(),
        KokoroBackend(),
        MeloTTSBackend(),
        GoogleNeural2WaveNetBackend(),
        GoogleChirp3HDBackend(),
    ]
    return {backend.id: backend for backend in backends}
