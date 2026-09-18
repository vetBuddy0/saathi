"""Piper — MIT, local, always available. The last-resort offline
fallback: whatever backend wins the comparison this module landed with,
Piper stays wired so there is always a voice that needs no network, no
credentials, and no GPU. Moved out of `voice/engine/cascade.py` (where it
used to be the only option) into its own `TTSBackend` implementation so
it sits behind the same interface as everything else in `voice/tts/`.
"""

from __future__ import annotations

import io
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable, Iterator

from piper import PiperVoice

from saathi.voice.language import SUPPORTED_LANGUAGES
from saathi.voice.tts import TTSBackend

_VOICE_DIR = Path.home() / ".saathi" / "tts-voices"

VoiceLoader = Callable[[str], PiperVoice]


def _ensure_voice_model(voice_name: str) -> Path:
    onnx_path = _VOICE_DIR / f"{voice_name}.onnx"
    if onnx_path.exists():
        return onnx_path
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "python3",
            "-m",
            "piper.download_voices",
            voice_name,
            "--download-dir",
            str(_VOICE_DIR),
        ],
        check=True,
    )
    return onnx_path


def _load_piper_voice(onnx_path: str) -> PiperVoice:
    return PiperVoice.load(onnx_path)


class PiperBackend(TTSBackend):
    id = "piper"
    display_name = "Piper (offline)"
    license = "MIT"
    local = True

    def __init__(self, voice_loader: VoiceLoader = _load_piper_voice) -> None:
        self._voice_loader = voice_loader
        self._voices: dict[str, PiperVoice] = {}
        self._voices_lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        return True, ""

    def preload(self, language: str) -> None:
        """Not part of `TTSBackend` — an extra `cascade.py` uses to warm
        the model during `end_turn()`'s network wait, which is what
        turned "interrupt landed mid-model-load" into "bounded by
        synthesis alone" (see cascade.py's docstring). Backends with no
        local model to load (Google) don't need this; a future local
        backend that does can add the same method without it being
        required by `TTSBackend` itself."""
        threading.Thread(target=self._voice_for, args=(language,), daemon=True).start()

    def _voice_for(self, language: str) -> PiperVoice:
        with self._voices_lock:
            if language not in self._voices:
                onnx_path = _ensure_voice_model(SUPPORTED_LANGUAGES[language])
                self._voices[language] = self._voice_loader(str(onnx_path))
            return self._voices[language]

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        voice = self._voice_for(language)
        for sentence in sentences:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize_wav(sentence, wav_file)
            yield buffer.getvalue()

    def cost_per_million_chars_usd(self) -> float:
        return 0.0
