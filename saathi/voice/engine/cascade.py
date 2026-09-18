"""The cascade `VoiceSession`: mic -> Groq Whisper -> Groq chat -> a
`TTSBackend` (`voice/tts/`) -> speaker.

One-hour spike (2026-09-17), built to close the loop end to end, not to
be the real engine: `GROQ_API_KEY` from the environment only, never
committed. The persona is `persona_stub.txt` next door — one hardcoded
paragraph, explicitly *not* the real identity file, which is being
written separately.

The brief asked for Kimi K2. The key this was tested with doesn't have
it (`GET /v1/models` doesn't list either `moonshotai/kimi-k2-instruct` or
`-0905`) — substituted `openai/gpt-oss-120b`, the strongest chat model
that account does have. Kimi K2 is still the intended model; swapping
back is a one-line constant change once access exists. (Retrying Kimi
K2 access is now explicitly item D's job — "not listed" may be a tier
issue, not permanent.)

Language: detection and the voice table share one source of truth,
`voice/language.py` — read that module first. `end_turn()` asks Groq
Whisper for its detected language (`response_format="verbose_json"`) and
resolves it through `resolve_language()` before doing anything with it;
the LLM is told which language to answer in explicitly rather than left
to infer it from the transcript alone, and `_speak()` picks its TTS
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

`_speak()` no longer synthesizes a whole reply in one blocking call. It
used to (single `synthesize_wav()` call for the entire text), and that
was a real bug, not filed debt: Piper's synthesis of a two-sentence
reply routinely takes 700ms+ on this laptop and several times longer on
a Raspberry Pi, and there was no way to abort it mid-call. That meant
the window where a press couldn't interrupt her was most of the reply on
Pi-class hardware, not a rare edge case at the start of a long one — a
correction the person building this made explicitly after the first
report of this file called it "occasional debt." The fix is
`TTSBackend.synthesize_stream()` (`voice/tts/__init__.py`): text is
split into sentences and each sentence is synthesized and played
separately, with `_interrupt_requested` checked under `_playback_lock`
both before asking for the next sentence's audio and before starting its
playback. That bounds the un-interruptible window to roughly one
sentence's synthesis time, for every backend, including ones (Piper,
Kokoro) with no streaming of their own. Verified against a real
long-reply barge-in after this landed; see `tests/test_cascade.py` and
the C report for the re-measured numbers.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from groq import Groq

from saathi.audio.playback import PlaybackHandle, play
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, resolve_language
from saathi.voice.tts import TTSBackend, split_into_sentences
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends

_PERSONA_PATH = Path(__file__).parent.parent / "persona_stub.txt"
_STT_MODEL = "whisper-large-v3-turbo"
# Kimi K2 (moonshotai/kimi-k2-instruct, -0905) is not available on the
# GROQ_API_KEY this was tested with -- confirmed via GET /v1/models,
# neither id is in that account's list. Substituted the strongest general
# chat model that IS on the key so the loop could close inside the hour.
# Flagged, not silently swapped: revisit once Kimi K2 access exists.
_LLM_MODEL = "openai/gpt-oss-120b"
_SAMPLE_RATE = 16000

BackendPreference = Callable[[], str]
LanguagePreference = Callable[[], "str | None"]


@dataclass(frozen=True)
class TurnTimings:
    """Item E's per-stage instrumentation, as much of it as this
    *non-streaming* cascade can honestly measure. `llm_ms` is the whole
    chat completion call, not "time to first token" — this architecture
    has no first token to time separately from the last one; a true
    streaming measurement needs `end_turn()` itself to stream, which is
    a `VoiceSession` contract change (one of CLAUDE.md's five protected
    interfaces) and not something to decide by renaming a field. Labeled
    honestly rather than as something it isn't.

    Not part of the `VoiceSession` Protocol — an additive capability
    `screen/server.py` reads via `getattr(session, "pop_last_turn_timings",
    None)`, the same pattern already used for `preload()`. A session that
    doesn't offer it (a fake in a test, a future realtime engine) simply
    doesn't contribute granular numbers; server.py degrades to logging
    what it can measure from the outside."""

    stt_ms: int
    llm_ms: int
    first_tts_chunk_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


def _no_language_preference() -> str | None:
    # Placeholder until item G's Ctrl+L panel / spoken "speak to me in
    # Mandarin" path write a real preference through IdentityStore (see
    # saathi/identity/preferences.py). Returning None means "nothing
    # stored yet" -- end_turn() already degrades sensibly for that case.
    return None


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _default_backend_preference() -> str:
    # Placeholder until item C/G's Ctrl+L panel writes a real preference
    # through IdentityStore -- see that work's tracking. Piper always
    # being the answer here is intentional, not a stub left dangling:
    # it's also the documented last-resort fallback, so "no preference
    # configured yet" and "fall back to the always-available backend"
    # are the same code path on purpose.
    return DEFAULT_BACKEND_ID


class CascadeSession:
    def __init__(
        self,
        sink_id: str,
        *,
        client: Groq | None = None,
        backends: dict[str, TTSBackend] | None = None,
        backend_preference: BackendPreference = _default_backend_preference,
        language_preference: LanguagePreference = _no_language_preference,
    ) -> None:
        if client is None:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("GROQ_API_KEY is not set in the environment")
            client = Groq(api_key=api_key)
        self._client = client
        self._backends = backends if backends is not None else default_backends()
        self._backend_preference = backend_preference
        self._language_preference = language_preference
        self._sink_id = sink_id
        self._persona = _PERSONA_PATH.read_text().strip()
        self._chunks: list[bytes] = []
        self._last_language = DEFAULT_LANGUAGE
        self._playback_lock = threading.Lock()
        self._current_playback: PlaybackHandle | None = None
        self._interrupt_requested = False
        self._pending_stt_ms: int | None = None
        self._pending_llm_ms: int | None = None
        self._pending_prompt_tokens: int | None = None
        self._pending_completion_tokens: int | None = None
        self._last_turn_timings: TurnTimings | None = None

    def pop_last_turn_timings(self) -> TurnTimings | None:
        """The previous turn's granular timings, consumed once — a
        second call before the next turn returns `None` rather than
        stale data from a turn that's already been logged. See
        `TurnTimings`'s docstring for why this exists outside the
        `VoiceSession` Protocol."""
        timings = self._last_turn_timings
        self._last_turn_timings = None
        return timings

    def _current_backend(self) -> TTSBackend:
        # Read fresh every call, not cached at construction -- this is
        # what makes a preference written through IdentityStore "take
        # effect on the next turn" for free, with no restart and no
        # explicit reload step.
        preferred_id = self._backend_preference()
        backend = self._backends.get(preferred_id)
        if backend is not None:
            available, _reason = backend.available()
            if available:
                return backend
        # Preferred backend missing, unknown, or unavailable right now:
        # fall back to Piper, which is always available (see
        # PiperBackend.available()) rather than raising and losing the
        # turn's reply entirely.
        return self._backends[DEFAULT_BACKEND_ID]

    def _preload_voice_in_background(self, language: str) -> None:
        # A real fix, not a speed hack: loading a local backend's voice
        # the first time can take seconds (measured against real
        # hardware; see check_barge_in's first run and
        # voice/tts/kokoro_backend.py's docstring), and end_turn()'s
        # STT/LLM round trip to Groq is otherwise dead time for the
        # audio side of this session. Loading during that wait, instead
        # of lazily on the first say(), is what turned a barge-in that
        # missed entirely (interrupt landed mid-*load*) into one bounded
        # by synthesis time alone. Not every backend has a model to
        # preload (Google has none), hence the getattr instead of a
        # required TTSBackend method.
        backend = self._current_backend()
        preload = getattr(backend, "preload", None)
        if preload is not None:
            preload(language)

    def start(self) -> None:
        self._chunks = []

    def send_audio(self, chunk: bytes) -> None:
        self._chunks.append(chunk)

    def end_turn(self) -> str:
        pcm = b"".join(self._chunks)
        self._chunks = []
        wav_bytes = _pcm_to_wav_bytes(pcm)

        # A stored preference (Ctrl+L panel, or eventually the spoken
        # "speak to me in Mandarin" tool path — item G) is read here, at
        # the start of the next turn, not pushed into a running session
        # directly — that's what makes "effective next turn, no
        # restart" fall out for free. It sets what resolve_language()
        # below falls back to; a *supported* live detection this turn
        # still wins over it, same as it already wins over whatever
        # self._last_language happened to be from ordinary conversation
        # (see voice/language.py — Saathi mirrors what she's actually
        # speaking). Only a *supported* preference is applied at all —
        # the same "never trust an unvalidated value" rule
        # resolve_language() already applies to a raw detection.
        preferred = self._language_preference()
        if preferred in SUPPORTED_LANGUAGES:
            self._last_language = preferred

        # Betting on last turn's language (or the preference just
        # applied above) for this turn's voice while the real answer
        # (this turn's detection, a few lines down) is still in flight.
        # Wrong on a language switch — falls back to the ordinary lazy
        # load in _speak(), just not warmed early that one time.
        self._preload_voice_in_background(self._last_language)

        stt_started_at = time.monotonic()
        transcription = self._client.audio.transcriptions.create(
            model=_STT_MODEL,
            file=("turn.wav", wav_bytes),
            response_format="verbose_json",
        )
        self._pending_stt_ms = round((time.monotonic() - stt_started_at) * 1000)
        heard = transcription.text.strip()
        detected = (transcription.language or "").lower()
        self._last_language = resolve_language(detected, self._last_language)

        llm_started_at = time.monotonic()
        completion = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": self._persona},
                {"role": "system", "content": f"Reply in {self._last_language}."},
                {"role": "user", "content": heard},
            ],
        )
        self._pending_llm_ms = round((time.monotonic() - llm_started_at) * 1000)
        usage = getattr(completion, "usage", None)
        self._pending_prompt_tokens = getattr(usage, "prompt_tokens", None)
        self._pending_completion_tokens = getattr(usage, "completion_tokens", None)
        return completion.choices[0].message.content.strip()

    def _speak(self, text: str) -> None:
        with self._playback_lock:
            self._interrupt_requested = False

        sentences = split_into_sentences(text)
        if not sentences:
            return

        backend = self._current_backend()
        stream = backend.synthesize_stream(self._last_language, sentences)
        speak_started_at = time.monotonic()
        first_chunk = True

        while True:
            with self._playback_lock:
                if self._interrupt_requested:
                    return  # interrupted since the last sentence: don't synthesize the next
            try:
                wav_bytes = next(stream)
            except StopIteration:
                return

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)
            try:
                tmp_path.write_bytes(wav_bytes)
                with self._playback_lock:
                    if self._interrupt_requested:
                        return  # interrupted during this sentence's synthesis
                    if first_chunk:
                        # Item E: real time-to-first-audio, not a whole-reply
                        # total — this is exactly what chunked synthesis
                        # (see voice/tts/__init__.py) makes measurable at
                        # all, since before it there was only ever one
                        # chunk covering the whole reply.
                        self._finalize_turn_timings(
                            first_tts_chunk_ms=round((time.monotonic() - speak_started_at) * 1000)
                        )
                        first_chunk = False
                    handle = play(self._sink_id, tmp_path)
                    self._current_playback = handle
                handle.wait()
            finally:
                with self._playback_lock:
                    self._current_playback = None
                tmp_path.unlink(missing_ok=True)

    def _finalize_turn_timings(self, first_tts_chunk_ms: int) -> None:
        # Only produces a result when end_turn() actually preceded this
        # say() call and set both pending values -- say() can be called
        # directly with no end_turn() before it (e.g. smoke.py's
        # check_barge_in, which isn't a real conversational turn), and
        # that must not log a turn with fabricated stt/llm numbers.
        if self._pending_stt_ms is None or self._pending_llm_ms is None:
            return
        self._last_turn_timings = TurnTimings(
            stt_ms=self._pending_stt_ms,
            llm_ms=self._pending_llm_ms,
            first_tts_chunk_ms=first_tts_chunk_ms,
            prompt_tokens=self._pending_prompt_tokens,
            completion_tokens=self._pending_completion_tokens,
        )
        self._pending_stt_ms = None
        self._pending_llm_ms = None
        self._pending_prompt_tokens = None
        self._pending_completion_tokens = None

    def say(self, text: str) -> None:
        self._speak(text)

    def on_audio(self, callback) -> None:
        raise NotImplementedError("streaming reply audio isn't built this hour")

    def on_intent(self, callback) -> None:
        raise NotImplementedError("tool-call intents aren't wired into a turn yet")

    def interrupt(self) -> None:
        """Barge-in's hook. If playback has already started, stops it
        right now, in whatever thread `say()` is blocked in. If `say()`
        is still inside a sentence's synthesis call, there's nothing to
        stop yet — instead this sets a flag `_speak()` checks before
        starting the next sentence's synthesis and again before its
        playback, so at most one already-in-flight sentence plays out."""
        with self._playback_lock:
            self._interrupt_requested = True
            if self._current_playback is not None:
                self._current_playback.stop()
