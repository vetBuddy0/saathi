"""Piper — MIT, local, available wherever `piper-tts` installs. The
last-resort offline fallback: whatever backend wins the comparison this
module landed with, Piper stays wired so there is always a voice that
needs no network, no credentials, and no GPU. Moved out of
`voice/engine/cascade.py` (where it used to be the only option) into
its own `TTSBackend` implementation so it sits behind the same
interface as everything else in `voice/tts/`.

"Always available" became "available where it installs" on 2026-10-08:
the engine also runs inside the Android app (Chaquopy), where
`piper-tts` has no wheel (`onnxruntime` underneath it), and the one
`from piper import PiperVoice` at the top of this module took the whole
registry -- and the screen server that imports it -- down with it. The
import is lazy now, the way `kokoro_backend.py`'s always was:
`available()` probes it and says "piper-tts is not installed" instead
of the module raising at import, so a phone lists Piper greyed out in
the panel like any other backend it hasn't got, and `cascade.py` falls
through to what is there. Linux with `piper-tts` installed is
byte-for-byte what it was. The option that lost: a registry that
leaves Piper out when it can't import. The panel would then not say why
Piper is missing, and `DEFAULT_BACKEND_ID` would name a key that may
not exist -- `cascade._current_backend()` now treats it as "if present
and available" instead.
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from saathi.voice.language import SUPPORTED_LANGUAGES
from saathi.voice.tts import TTSBackend

if TYPE_CHECKING:
    from piper import PiperVoice

_VOICE_DIR = Path.home() / ".saathi" / "tts-voices"

VoiceLoader = Callable[[str], "PiperVoice"]

_NOT_INSTALLED_REASON = "piper-tts is not installed"


def _piper_importable() -> tuple[bool, str]:
    """The probe `available()` gates on. Module-level, like
    `google_backend._client_importable()`, so a test can stand in the
    phone's state without uninstalling anything."""
    try:
        import piper  # noqa: F401
    except ImportError:
        return False, _NOT_INSTALLED_REASON
    return True, ""


def _ensure_voice_model(voice_name: str) -> Path:
    onnx_path = _VOICE_DIR / f"{voice_name}.onnx"
    if onnx_path.exists():
        return onnx_path
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            # sys.executable, not the bare string "python3" -- found by
            # real testing, not inspection: a plain "python3" resolves
            # against $PATH, which is not guaranteed to be this same
            # interpreter (it resolved to an unrelated Anaconda install
            # with no `piper` package on this very machine). Using this
            # process's own interpreter is what setup-pi.sh already gets
            # right by going through `uv run python3 ...` for the same
            # download; this fixes the same class of bug for the path
            # that runs outside setup-pi.sh's `uv run` wrapper.
            sys.executable,
            "-m",
            "piper.download_voices",
            voice_name,
            "--download-dir",
            str(_VOICE_DIR),
        ],
        check=True,
    )
    return onnx_path


def _load_piper_voice(onnx_path: str) -> "PiperVoice":
    from piper import PiperVoice

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
        return _piper_importable()

    def preload(self, language: str) -> None:
        """Not part of `TTSBackend` — an extra `cascade.py` uses to warm
        the model during `end_turn()`'s network wait, which is what
        turned "interrupt landed mid-model-load" into "bounded by
        synthesis alone" (see cascade.py's docstring). Backends with no
        local model to load (Google) don't need this; a future local
        backend that does can add the same method without it being
        required by `TTSBackend` itself."""
        if not _piper_importable()[0]:
            return  # nothing to warm; _voice_for would only raise in a thread
        threading.Thread(target=self._voice_for, args=(language,), daemon=True).start()

    def _voice_for(self, language: str) -> PiperVoice:
        with self._voices_lock:
            if language not in self._voices:
                importable, reason = _piper_importable()
                if not importable:
                    # Never chosen while unavailable (cascade.py); said
                    # plainly if something asks anyway, rather than a
                    # download subprocess failing on `-m piper`.
                    raise RuntimeError(f"{self.id}: {reason}")
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
