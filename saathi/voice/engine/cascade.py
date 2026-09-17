"""The cascade `VoiceSession`: mic -> Groq Whisper -> Groq chat -> Piper
-> speaker.

One-hour spike (2026-09-17), built to close the loop end to end, not to
be the real engine: `GROQ_API_KEY` from the environment only, never
committed. The persona is `persona_stub.txt` next door — one hardcoded
paragraph, explicitly *not* the real identity file, which is being
written separately. Piper over Kokoro: Kokoro pulls torch unconditionally
and Piper installed and synthesized in under ten seconds with real
aarch64/CPU support, so the time budget picked it rather than a trial
that risked blowing the hour.

The brief asked for Kimi K2. The key this was tested with doesn't have
it (`GET /v1/models` doesn't list either `moonshotai/kimi-k2-instruct` or
`-0905`) — substituted `openai/gpt-oss-120b`, the strongest chat model
that account does have, flagged rather than swapped quietly. Swapping it
back is a one-line constant change once access exists.

Ships without tests on purpose — this is debt, on the record: checkpoint
2 is not done until barge-in, memory, cost logging, retrieval, and
reflection are back in, and until this file has tests. Nothing here
should be mistaken for "finished."
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import wave
from pathlib import Path

from groq import Groq
from piper import PiperVoice

from saathi.audio.playback import play

_PERSONA_PATH = Path(__file__).parent.parent / "persona_stub.txt"
_VOICE_NAME = "en_US-amy-medium"
_VOICE_DIR = Path.home() / ".saathi" / "tts-voices"
_STT_MODEL = "whisper-large-v3-turbo"
# Kimi K2 (moonshotai/kimi-k2-instruct, -0905) is not available on the
# GROQ_API_KEY this was tested with -- confirmed via GET /v1/models,
# neither id is in that account's list. Substituted the strongest general
# chat model that IS on the key so the loop could close inside the hour.
# Flagged, not silently swapped: revisit once Kimi K2 access exists.
_LLM_MODEL = "openai/gpt-oss-120b"
_SAMPLE_RATE = 16000


def _ensure_voice_model() -> Path:
    onnx_path = _VOICE_DIR / f"{_VOICE_NAME}.onnx"
    if onnx_path.exists():
        return onnx_path
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "python3",
            "-m",
            "piper.download_voices",
            _VOICE_NAME,
            "--download-dir",
            str(_VOICE_DIR),
        ],
        check=True,
    )
    return onnx_path


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class CascadeSession:
    def __init__(self, sink_id: str) -> None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set in the environment")
        self._client = Groq(api_key=api_key)
        self._voice = PiperVoice.load(str(_ensure_voice_model()))
        self._sink_id = sink_id
        self._persona = _PERSONA_PATH.read_text().strip()
        self._chunks: list[bytes] = []

    def start(self) -> None:
        self._chunks = []

    def send_audio(self, chunk: bytes) -> None:
        self._chunks.append(chunk)

    def end_turn(self) -> str:
        pcm = b"".join(self._chunks)
        self._chunks = []
        wav_bytes = _pcm_to_wav_bytes(pcm)

        transcription = self._client.audio.transcriptions.create(
            model=_STT_MODEL,
            file=("turn.wav", wav_bytes),
        )
        heard = transcription.text.strip()

        completion = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": self._persona},
                {"role": "user", "content": heard},
            ],
        )
        return completion.choices[0].message.content.strip()

    def _speak(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            with wave.open(str(tmp_path), "wb") as wav_file:
                self._voice.synthesize_wav(text, wav_file)
            play(self._sink_id, tmp_path).wait()
        finally:
            tmp_path.unlink(missing_ok=True)

    # Declared by VoiceSession, not used this hour.
    def say(self, text: str) -> None:
        self._speak(text)

    def on_audio(self, callback) -> None:
        raise NotImplementedError("streaming reply audio isn't built this hour")

    def on_intent(self, callback) -> None:
        raise NotImplementedError("tool-call intents aren't wired into a turn yet")

    def interrupt(self) -> None:
        raise NotImplementedError("barge-in is deliberately not built this hour")
