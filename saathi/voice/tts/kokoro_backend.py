"""Kokoro-82M (Apache 2.0), local. Real installable alternative to Piper
on this project's pinned Python (3.12) and target arch (aarch64) -- a
genuine `manylinux_2_28_aarch64` `torch` wheel exists (454MB, confirmed
against PyPI's own file listing, not assumed) -- but it is not free the
way Piper is: `torch` is an unconditional dependency, and model weights
(~330MB) download from Hugging Face on first use, not at install time.
Measured on this laptop against an empty HF cache: 94s to build the
pipeline once, 3.6s to first audio after that. Both numbers are
one-laptop, one-run measurements, not a benchmark; see the C comparison
report for the real cross-backend numbers and whatever the Pi produces.

Language support does not line up with `voice/language.py`'s four
languages: Kokoro-82M ships voices for English, Spanish, French, Hindi,
Italian, Japanese, Brazilian Portuguese, and Mandarin -- Bengali is not
among them. `synthesize_stream()` raises a plain `ValueError` naming the
gap rather than silently degrading to English, on the same "unavailable
and visibly so beats silently broken" principle as `voice/language.py`
itself. Whatever selects a backend (the Ctrl+L panel, in item C/G) needs
to know this before offering Kokoro for Bengali, not discover it from a
stack trace mid-turn.

Optional dependency group: `kokoro` in pyproject.toml. Not installed by
default or in CI -- 454MB of torch is not something every contributor or
CI run should have to pull down to run the test suite, and `available()`
reports its absence as an ordinary, expected unavailability reason
rather than an import error crashing anything that merely imports this
module.
"""

from __future__ import annotations

import io
import threading
import wave
from typing import Iterator

from saathi.voice.tts import TTSBackend

_SAMPLE_RATE = 24000  # Kokoro's fixed output rate

# voice/language.py's keys -> Kokoro's lang_code + a default voice id.
# Bengali is deliberately absent -- see module docstring.
_LANGUAGE_TO_KOKORO = {
    "english": ("a", "af_heart"),  # American English
    "chinese": ("z", "zf_xiaobei"),  # Mandarin
    "hindi": ("h", "hf_alpha"),
}


def _floats_to_wav_bytes(samples, sample_rate: int = _SAMPLE_RATE) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


class KokoroBackend(TTSBackend):
    id = "kokoro"
    display_name = "Kokoro-82M (offline)"
    license = "Apache 2.0"
    local = True

    def __init__(self) -> None:
        self._pipelines: dict[str, object] = {}
        self._pipelines_lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        try:
            import kokoro  # noqa: F401
        except ImportError:
            return False, "kokoro is not installed (optional dependency group 'kokoro')"
        return True, ""

    def preload(self, language: str) -> None:
        """Same rationale as `PiperBackend.preload()`: warm the pipeline
        during `end_turn()`'s network wait instead of on the first
        `say()`, since building it the first time measured ~94s
        (dominated by an unauthenticated Hugging Face download) on this
        laptop."""
        if language not in _LANGUAGE_TO_KOKORO:
            return
        threading.Thread(target=self._pipeline_for, args=(language,), daemon=True).start()

    def _pipeline_for(self, language: str):
        from kokoro import KPipeline

        lang_code, _voice = _LANGUAGE_TO_KOKORO[language]
        with self._pipelines_lock:
            if lang_code not in self._pipelines:
                self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
            return self._pipelines[lang_code]

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        if language not in _LANGUAGE_TO_KOKORO:
            supported = ", ".join(sorted(_LANGUAGE_TO_KOKORO))
            raise ValueError(
                f"Kokoro has no voice for {language!r}; supported: {supported}"
            )
        pipeline = self._pipeline_for(language)
        _lang_code, voice = _LANGUAGE_TO_KOKORO[language]
        for sentence in sentences:
            chunks = [audio for _gs, _ps, audio in pipeline(sentence, voice=voice)]
            import numpy as np

            joined = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            yield _floats_to_wav_bytes(joined)

    def cost_per_million_chars_usd(self) -> float:
        return 0.0
