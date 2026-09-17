"""The cascade `VoiceSession`: mic -> Groq Whisper -> Groq chat -> Piper
-> speaker.

One-hour spike (2026-09-17), built to close the loop end to end, not to
be the real engine: `GROQ_API_KEY` from the environment only, never
committed. The persona is `persona_stub.txt` next door — one hardcoded
paragraph, explicitly *not* the real identity file, which is being
written separately.

Piper over Kokoro, still: Kokoro pulls `torch` unconditionally, and that
is not just a "would have blown the 15-minute trial window" problem —
it's a real problem for the Raspberry Pi this is meant to run on, where a
multi-hundred-MB unconditional dependency is expensive on both disk and
first-boot time. Piper installed and synthesized in under ten seconds
with plain CPU support and no such cost. Anyone retrying Kokoro later
should know that going in, not rediscover it.

The brief asked for Kimi K2. The key this was tested with doesn't have
it (`GET /v1/models` doesn't list either `moonshotai/kimi-k2-instruct` or
`-0905`) — substituted `openai/gpt-oss-120b`, the strongest chat model
that account does have. Kimi K2 is still the intended model; swapping
back is a one-line constant change once access exists.

Language: detection and the voice table share one source of truth,
`voice/language.py` — read that module first. `end_turn()` asks Groq
Whisper for its detected language (`response_format="verbose_json"`) and
resolves it through `resolve_language()` before doing anything with it;
the LLM is told which language to answer in explicitly rather than left
to infer it from the transcript alone, and `_speak()` picks its Piper
voice from that same resolved value — never from the raw detection.

Ships without tests on purpose in its first commit — that was debt, on
the record. Tests landed the same session; see `tests/test_cascade.py`.
Still not done: memory, cost logging, retrieval, reflection.

`interrupt()` is real now, not a stub: it calls `.stop()` on whatever
`PlaybackHandle` `_speak()` is currently blocked on `.wait()`-ing for, in
whatever thread called `say()` (always a different thread than
`interrupt()`'s caller — `screen/server.py` runs the turn in an executor
so the event loop stays responsive, and calls `interrupt()` from the
event loop thread while that executor thread is still blocked).

`_interrupt_requested` exists because of a real, not theoretical, race:
`synthesize_wav()` for a two-sentence reply routinely takes 700ms+ on
this machine — longer than the 500ms `saathi smoke --barge-in` interrupts
at — so `interrupt()` regularly lands *before* `_current_playback` is
set, with nothing yet to `.stop()`. The first version of this file
shipped without that flag and documented the gap instead of closing
it; `saathi smoke --barge-in`'s first real run turned "narrow window,
rarely hit" into "hit on the very first try," which is the difference
between a caveat and a bug. `_speak()` now checks the flag once
synthesis finishes and skips starting playback at all if it's set,
under the same lock — an interrupt during synthesis now means "never
play this," not "stop nothing, then play anyway."
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable

from groq import Groq
from piper import PiperVoice

from saathi.audio.playback import PlaybackHandle, play
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, resolve_language

_PERSONA_PATH = Path(__file__).parent.parent / "persona_stub.txt"
_VOICE_DIR = Path.home() / ".saathi" / "tts-voices"
_STT_MODEL = "whisper-large-v3-turbo"
# Kimi K2 (moonshotai/kimi-k2-instruct, -0905) is not available on the
# GROQ_API_KEY this was tested with -- confirmed via GET /v1/models,
# neither id is in that account's list. Substituted the strongest general
# chat model that IS on the key so the loop could close inside the hour.
# Flagged, not silently swapped: revisit once Kimi K2 access exists.
_LLM_MODEL = "openai/gpt-oss-120b"
_SAMPLE_RATE = 16000

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


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _load_piper_voice(onnx_path: str) -> PiperVoice:
    return PiperVoice.load(onnx_path)


class CascadeSession:
    def __init__(
        self,
        sink_id: str,
        *,
        client: Groq | None = None,
        voice_loader: VoiceLoader = _load_piper_voice,
    ) -> None:
        if client is None:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("GROQ_API_KEY is not set in the environment")
            client = Groq(api_key=api_key)
        self._client = client
        self._voice_loader = voice_loader
        self._voices: dict[str, PiperVoice] = {}
        self._voices_lock = threading.Lock()
        self._sink_id = sink_id
        self._persona = _PERSONA_PATH.read_text().strip()
        self._chunks: list[bytes] = []
        self._last_language = DEFAULT_LANGUAGE
        self._playback_lock = threading.Lock()
        self._current_playback: PlaybackHandle | None = None
        self._interrupt_requested = False

    def _voice_for(self, language: str) -> PiperVoice:
        with self._voices_lock:
            if language not in self._voices:
                onnx_path = _ensure_voice_model(SUPPORTED_LANGUAGES[language])
                self._voices[language] = self._voice_loader(str(onnx_path))
            return self._voices[language]

    def _preload_voice_in_background(self, language: str) -> None:
        # A real fix, not a speed hack: loading a Piper voice the first
        # time takes seconds (measured against real hardware; see
        # check_barge_in's first run), and end_turn()'s STT/LLM round
        # trip to Groq is otherwise dead time for the audio side of this
        # session. Loading during that wait, instead of lazily on the
        # first say(), is what turned a barge-in that missed entirely
        # (interrupt landed mid-*load*) into one bounded by synthesis
        # time alone.
        threading.Thread(target=self._voice_for, args=(language,), daemon=True).start()

    def start(self) -> None:
        self._chunks = []

    def send_audio(self, chunk: bytes) -> None:
        self._chunks.append(chunk)

    def end_turn(self) -> str:
        pcm = b"".join(self._chunks)
        self._chunks = []
        wav_bytes = _pcm_to_wav_bytes(pcm)

        # Betting on last turn's language for this turn's voice while
        # the real answer (this turn's detection, a few lines down) is
        # still in flight. Wrong on a language switch — falls back to
        # the ordinary lazy load in _speak(), just not warmed early that
        # one time.
        self._preload_voice_in_background(self._last_language)

        transcription = self._client.audio.transcriptions.create(
            model=_STT_MODEL,
            file=("turn.wav", wav_bytes),
            response_format="verbose_json",
        )
        heard = transcription.text.strip()
        detected = (transcription.language or "").lower()
        self._last_language = resolve_language(detected, self._last_language)

        completion = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": self._persona},
                {"role": "system", "content": f"Reply in {self._last_language}."},
                {"role": "user", "content": heard},
            ],
        )
        return completion.choices[0].message.content.strip()

    def _speak(self, text: str) -> None:
        with self._playback_lock:
            self._interrupt_requested = False

        voice = self._voice_for(self._last_language)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            with wave.open(str(tmp_path), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)  # can take 700ms+ — see docstring

            with self._playback_lock:
                if self._interrupt_requested:
                    return  # interrupted during synthesis: never start playback
                handle = play(self._sink_id, tmp_path)
                self._current_playback = handle
            handle.wait()
        finally:
            with self._playback_lock:
                self._current_playback = None
            tmp_path.unlink(missing_ok=True)

    def say(self, text: str) -> None:
        self._speak(text)

    def on_audio(self, callback) -> None:
        raise NotImplementedError("streaming reply audio isn't built this hour")

    def on_intent(self, callback) -> None:
        raise NotImplementedError("tool-call intents aren't wired into a turn yet")

    def interrupt(self) -> None:
        """Barge-in's hook. If playback has already started, stops it
        right now, in whatever thread `say()` is blocked in. If `say()`
        is still inside `synthesize_wav()`, there's nothing to stop yet —
        instead this sets a flag `_speak()` checks the moment synthesis
        finishes, so playback never starts at all."""
        with self._playback_lock:
            self._interrupt_requested = True
            if self._current_playback is not None:
                self._current_playback.stop()
