"""The cascade `VoiceSession`: mic -> Groq Whisper -> Groq chat -> a
`TTSBackend` (`voice/tts/`) -> speaker.

One-hour spike (2026-09-17), built to close the loop end to end, not to
be the real engine: `GROQ_API_KEY` from the environment only, never
committed.

Item F: the persona is no longer a static read of `persona_stub.txt`.
`identity/compile.py`'s `compile_context()` is now the actual source of
what the engine receives — it reads that same file as its base layer
and folds in whatever `IdentityStore` holds on top of it (still nothing,
today — see that module's docstring). `_refresh_compiled_context_in_background()`
is where SPEC.md's "compiled between turns, never during one" is
actually enforced: once at construction, and again after every `say()`
call returns, in a background thread — so the *next* `end_turn()` reads
context compiled from the turn that just finished, one turn stale on
purpose. Passing no `identity_store` keeps the old static behavior
exactly (`persona_stub.txt`, read once, never refreshed) — every
existing caller that doesn't know about `IdentityStore` yet, including
several tests, still works unchanged.

The brief asked for Kimi K2. Retried per item D's instruction — still
not on this account, and neither is llama-3.3-70b-versatile or
qwen/qwen3-32b, the other two originally requested models (all
confirmed live against `GET /v1/models`, 2026-09-18). `_LLM_MODEL` is
now `qwen/qwen3.8-27b`, decided after a real head-to-head bake-off
against the four models actually available — see that constant's own
comment and `docs/completed/D-model-bakeoff.md` for the reasoning.

Language: detection and the voice table share one source of truth,
`voice/language.py` — read that module first. `end_turn()` asks Groq
Whisper for its detected language (`response_format="verbose_json"`) and
resolves it through `resolve_language()` before doing anything with it;
the LLM is told which language to answer in explicitly rather than left
to infer it from the transcript alone, and `_speak()` picks its TTS
voice from that same resolved value — never from the raw detection.

Ships without tests on purpose in its first commit — that was debt, on
the record. Tests landed the same session; see `tests/test_cascade.py`.
Memory, retrieval and cost logging landed in items F/E — see
`identity/compile.py` and `TurnTimings` below. Reflection
(`identity/reflect.py`) is checkpoint 3 scope, not this file's.

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
both before waiting on the next sentence's audio and before starting its
playback. That bounds the un-interruptible window to roughly one
sentence's synthesis time, for every backend, including ones (Piper,
Kokoro) with no streaming of their own. Verified against a real
long-reply barge-in after this landed; see `tests/test_cascade.py` and
the C report for the re-measured numbers.

Pipelined since 2026-09-19: `_prefetch_next_chunk()` starts sentence
N+1's synthesis the moment sentence N is pulled off the pipeline, so it
overlaps with N's *playback* instead of starting only after N is done
playing — this is what actually cuts inter-sentence silence, not just
first-sentence latency. Responsiveness is unaffected: an interrupt still
stops playback and returns from `_speak()` immediately, never waiting
on an abandoned prefetch (see that function's docstring for why a
daemon thread, not a `ThreadPoolExecutor`). What *does* change slightly:
synthesis may now run up to one sentence ahead of what's playing even
after an interrupt lands, since that prefetch was already kicked off
before the interrupt could be seen. Never two sentences ahead — the
next prefetch after that one only starts once the current one is
actually consumed, which an interrupted turn never reaches.
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from groq import Groq

from saathi.audio.playback import PlaybackHandle, play
from saathi.audio.vad import contains_speech
from saathi.identity.compile import compile_context
from saathi.identity.digest import digest_turn, write_episode
from saathi.identity.store import IdentityStore
from saathi.voice.conversation import ConversationMemory, Exchange
from saathi.voice.language import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, resolve_language
from saathi.voice.tts import TTSBackend, split_into_sentences
from saathi.voice.tts.registry import DEFAULT_BACKEND_ID, default_backends

logger = logging.getLogger(__name__)

_PERSONA_PATH = Path(__file__).parent.parent / "persona_stub.txt"
_STT_MODEL = "whisper-large-v3-turbo"
# Item D's bake-off (2026-09-18), decided: none of the originally
# requested models (llama-3.3-70b-versatile, qwen/qwen3-32b, Kimi K2)
# exist on this Groq account anymore -- confirmed live against
# GET /v1/models. Of the four real, available alternatives tested head
# to head on the same four prompts, qwen/qwen3.8-27b was the only one
# that didn't fabricate either a memory or weather data, was the
# fastest, and its token usage tracks what it actually says rather than
# hiding a reasoning-token cost multiplier -- gpt-oss-20b, previously
# the runner-up candidate, invented a fake memory about a daughter's
# visit, which is disqualifying for a device talking to someone with
# memory problems. See DECISIONS.md and docs/completed/D-model-bakeoff.md.
_LLM_MODEL = "qwen/qwen3.8-27b"
# Checked against console.groq.com/docs/models on 2026-09-18. LLM-only:
# does not include Whisper STT or TTS backend cost, which are priced in
# different units (audio-seconds, characters) and tracked separately --
# see voice/tts/*_backend.py's cost_per_million_chars_usd() for TTS.
_LLM_PRICE_PER_MILLION_USD = {"input": 0.80, "output": 4.00}
_SAMPLE_RATE = 16000


def _estimate_llm_cost_usd(
    prompt_tokens: int | None, completion_tokens: int | None
) -> float | None:
    if prompt_tokens is None or completion_tokens is None:
        return None
    return (
        prompt_tokens * _LLM_PRICE_PER_MILLION_USD["input"] / 1_000_000
        + completion_tokens * _LLM_PRICE_PER_MILLION_USD["output"] / 1_000_000
    )


BackendPreference = Callable[[], str]
LanguagePreference = Callable[[], "str | None"]


@dataclass(frozen=True)
class TurnTimings:
    """Item E's per-stage instrumentation, as much of it as this
    *non-streaming* cascade can honestly measure. `first_token_ms` is
    named to match `turns.first_token_ms` (SPEC.md), but it's really the
    whole chat completion call's duration, not a true first-token
    measurement — this architecture has no first token to time
    separately from the last one; a true streaming measurement needs
    `end_turn()` itself to stream, which is a `VoiceSession` contract
    change (one of CLAUDE.md's five protected interfaces) and not
    something to decide by renaming a field. Documented honestly rather
    than pretending the column name alone makes it real.

    Not part of the `VoiceSession` Protocol — an additive capability
    `screen/server.py` reads via `getattr(session, "pop_last_turn_timings",
    None)`, the same pattern already used for `preload()`. A session that
    doesn't offer it (a fake in a test, a future realtime engine) simply
    doesn't contribute granular numbers; server.py degrades to logging
    what it can measure from the outside."""

    stt_ms: int
    first_token_ms: int
    first_tts_chunk_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None


def _no_language_preference() -> str | None:
    # Placeholder until item G's Ctrl+L panel / spoken "speak to me in
    # Mandarin" path write a real preference through IdentityStore (see
    # saathi/identity/preferences.py). Returning None means "nothing
    # stored yet" -- end_turn() already degrades sensibly for that case.
    return None


def _pcm_to_flac_bytes(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """What `end_turn()` uploads to Whisper. Used to be a raw WAV built
    with the stdlib `wave` module; real, live measurement against the
    Whisper endpoint (2026-09-18, a ~3.9s clip) found FLAC's smaller
    upload measurably faster: median 282ms for WAV vs. 251ms for FLAC
    (~46% smaller, same lossless audio) — see
    docs/completed/latency-investigation.md. Opus was faster still
    (234ms) but needs PyAV/ffmpeg, a much heavier dependency than
    `soundfile`/libsndfile for a further ~17ms; not worth it as the
    default."""
    samples = np.frombuffer(pcm, dtype="<i2")
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()


_STREAM_DONE = object()


def _prefetch_next_chunk(stream) -> "queue.Queue":
    """Kicks off synthesis of the *next* sentence in a daemon thread,
    right now, and returns a one-item `Queue` that will hold its result
    (or `_STREAM_DONE`). `_speak()` calls this immediately after pulling
    a chunk off the previous prefetch, so sentence N+1's synthesis runs
    while sentence N is still playing — that overlap is the whole point
    (pipelining, not read-ahead-and-buffer: at most one sentence is ever
    being synthesized ahead of what's currently playing, since the next
    prefetch isn't started until the previous one has already been
    consumed).

    A daemon `threading.Thread`, not a `ThreadPoolExecutor` used as a
    context manager: an executor's `__exit__` calls `shutdown(wait=True)`,
    which blocks until in-flight work finishes — exactly the wrong thing
    when `_speak()` returns early on interrupt with a prefetch still
    running. This way an abandoned prefetch (interrupted before its
    chunk was ever needed) is simply discarded; `_speak()` returns
    immediately, not after Piper finishes a synthesis call nobody is
    going to play. Only ever one `next(stream)` call in flight at a
    time — safe to call on a plain generator, which isn't otherwise
    thread-safe to access concurrently."""
    result: queue.Queue = queue.Queue(maxsize=1)

    def _run() -> None:
        result.put(next(stream, _STREAM_DONE))

    threading.Thread(target=_run, daemon=True).start()
    return result


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
        identity_store: IdentityStore | None = None,
        tool_schemas: list[dict] | None = None,
        speech_gate: Callable[[bytes], bool] | None = None,
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
        self._identity_store = identity_store
        # No store: exactly the old, static behavior -- persona_stub.txt,
        # read once, never refreshed. A store: compile_context() runs
        # once here (there's no previous turn to trigger a background
        # refresh from, and SPEC.md says context must already be held
        # "when she starts speaking") and again after every say() call.
        if identity_store is not None:
            self._compiled_context = compile_context(identity_store)
        else:
            self._compiled_context = _PERSONA_PATH.read_text().strip()
        self._chunks: list[bytes] = []
        self._last_language = DEFAULT_LANGUAGE
        self._playback_lock = threading.Lock()
        self._current_playback: PlaybackHandle | None = None
        self._interrupt_requested = False
        self._pending_stt_ms: int | None = None
        self._pending_first_token_ms: int | None = None
        self._pending_prompt_tokens: int | None = None
        self._pending_completion_tokens: int | None = None
        self._last_turn_timings: TurnTimings | None = None
        self._tool_schemas = tool_schemas
        self._intent_callback = None
        # None means the real Silero gate (audio/vad.py's contains_speech);
        # tests inject a permissive one. Same shape as `client`/`backends`:
        # the default is the real thing, never a stub.
        self._speech_gate = speech_gate if speech_gate is not None else contains_speech
        self._conversation = ConversationMemory()
        # Set by end_turn(), consumed by say()'s tail: the exchange that
        # just happened, waiting to be recorded once it's actually been
        # spoken. None when say() is called without an end_turn() before
        # it (smoke.py's barge-in check), which then records nothing.
        self._pending_exchange: Exchange | None = None
        # Warm the current backend's voice right now, not on the first
        # end_turn() -- see this module's docstring on cold starts.
        # Without this, only the *second* reply onward benefited from a
        # warm voice; the very first turn of a fresh process ate the
        # full cold-load cost on top of its own synthesis.
        self._preload_voice_in_background(self._last_language)

    def pop_last_turn_timings(self) -> TurnTimings | None:
        """The previous turn's granular timings, consumed once — a
        second call before the next turn returns `None` rather than
        stale data from a turn that's already been logged. See
        `TurnTimings`'s docstring for why this exists outside the
        `VoiceSession` Protocol."""
        timings = self._last_turn_timings
        self._last_turn_timings = None
        return timings

    def _refresh_compiled_context_in_background(self) -> None:
        # SPEC.md: "Context is compiled between turns, never during
        # one." Called once a turn is fully over (see _speak()'s tail),
        # in a background thread so a slow compile (scoring many
        # episodes) never adds latency to the turn that's already
        # finishing. The *next* end_turn() reads self._compiled_context
        # whenever it happens to run -- one turn stale on purpose, per
        # SPEC.md's own words, not a race to close.
        if self._identity_store is None:
            return

        # A fresh connection to the same file, not the shared one --
        # found by real testing, not inspection: sqlite3 connections
        # can't cross threads (ProgrammingError), and flipping
        # check_same_thread off on the shared connection would change
        # IdentityStore's threading contract for every caller just to
        # suit this one. See IdentityStore.path's docstring.
        store_path = self._identity_store.path

        def _refresh() -> None:
            with IdentityStore(store_path) as background_store:
                self._compiled_context = compile_context(background_store)

        threading.Thread(target=_refresh, daemon=True).start()

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
        # The silence bug, fixed where it starts. Whisper hallucinates
        # stock phrases (" Thank you.") on silence and reports
        # no_speech_prob=0.0 while doing it -- measured, see
        # audio/vad.py's contains_speech(). So the question "did she
        # say anything" is asked of the audio, before any STT call is
        # spent on it. An empty string is the contract for "nothing
        # said": screen/server.py ends the turn silently on it, back to
        # IDLE, and never enters SPEAKING. Not a spoken "I didn't catch
        # that" -- she would say it every time a door closed.
        if not self._speech_gate(pcm):
            return ""
        flac_bytes = _pcm_to_flac_bytes(pcm)

        # A stored preference (Ctrl+L panel, or the spoken "speak to me
        # in Mandarin" tool path — item G) is read here, at the start of
        # the next turn, not pushed into a running session directly —
        # that's what makes "effective next turn, no restart" fall out
        # for free. A *supported* preference now pins the language for
        # every following turn, overriding organic detection, until it's
        # explicitly changed again (another preference write) — not
        # merely a fallback default for detection to override.
        #
        # This was a real, verified bug, not a hypothetical: an earlier
        # version let a supported live detection win over the
        # preference every time, same as it already won over an
        # unset self._last_language from ordinary conversation. That
        # made "speak to me in Mandarin" invisible the instant she next
        # spoke a sentence Whisper detected as English — the overwhelmingly
        # common case, since asking for the switch happens in whatever
        # language she was already speaking. "Effective next turn" means
        # every next turn, not "until she next speaks the old language,"
        # which is indistinguishable from not switching at all. An
        # *unsupported* preference is still ignored outright, same as an
        # unsupported detection always has been (voice/language.py's
        # Portuguese-reply bug fix) — this only changes the *supported*
        # case's priority against detection, nothing about what counts
        # as trustworthy in the first place.
        preferred = self._language_preference()
        language_pinned = preferred in SUPPORTED_LANGUAGES
        if language_pinned:
            self._last_language = preferred

        # Betting on last turn's language (or the pinned preference just
        # applied above) for this turn's voice while the real answer
        # (this turn's detection, a few lines down) is still in flight.
        # Wrong on an organic, unpinned language switch — falls back to
        # the ordinary lazy load in _speak(), just not warmed early that
        # one time.
        self._preload_voice_in_background(self._last_language)

        stt_started_at = time.monotonic()
        transcription = self._client.audio.transcriptions.create(
            model=_STT_MODEL,
            file=("turn.flac", flac_bytes),
            response_format="verbose_json",
        )
        self._pending_stt_ms = round((time.monotonic() - stt_started_at) * 1000)
        heard = transcription.text.strip()
        if not heard:
            # The gate heard voice but Whisper made nothing of it. Same
            # contract as the gate: nothing said, end silently.
            self._pending_stt_ms = None
            return ""
        if not language_pinned:
            detected = (transcription.language or "").lower()
            self._last_language = resolve_language(detected, self._last_language)

        # Three layers of memory, oldest to newest, then her words.
        # Layer 3 (episodes, via compile_context) and layer 2 (the
        # running summary) are system context; layer 1 (the verbatim
        # window) is real prior turns, as the model saw them. See
        # voice/conversation.py for why verbatim and why capped.
        messages: list[dict] = [
            {"role": "system", "content": self._compiled_context},
        ]
        summary = self._conversation.summary
        if summary:
            messages.append(
                {"role": "system", "content": f"Earlier in this conversation: {summary}"}
            )
        messages.append({"role": "system", "content": f"Reply in {self._last_language}."})
        messages.extend(self._conversation.messages())
        messages.append({"role": "user", "content": heard})

        llm_started_at = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0

        completion = self._client.chat.completions.create(
            model=_LLM_MODEL,
            messages=messages,
            tools=self._tool_schemas or None,
        )
        message = completion.choices[0].message
        usage = getattr(completion, "usage", None)
        prompt_tokens += getattr(usage, "prompt_tokens", None) or 0
        completion_tokens += getattr(usage, "completion_tokens", None) or 0

        if message.tool_calls:
            # "The voice engine never executes anything. It emits
            # intent; the core validates; the tool executes" (SPEC.md).
            # _emit_intent() only calls whatever was registered through
            # on_intent() -- it never touches a Tool or Registry itself.
            # Handles only the first tool call; a model asking for two
            # in one turn is a real, unhandled edge case here, not
            # silently mishandled — flagged, not built, since
            # set_language is this project's only real tool so far and
            # nothing exercises multi-call turns yet.
            tool_call = message.tool_calls[0]
            reply_text, follow_up_prompt_tokens, follow_up_completion_tokens = (
                self._continue_after_tool_call(messages, message, tool_call)
            )
            prompt_tokens += follow_up_prompt_tokens
            completion_tokens += follow_up_completion_tokens
        else:
            reply_text = (message.content or "").strip()

        self._pending_first_token_ms = round((time.monotonic() - llm_started_at) * 1000)
        self._pending_prompt_tokens = prompt_tokens
        self._pending_completion_tokens = completion_tokens
        # The real count from the API, not an estimate. A flat number
        # here across turns is the tell that history isn't being sent.
        logger.info(
            "prompt tokens: %d (window: %d exchanges, summary: %d chars)",
            prompt_tokens,
            len(self._conversation.window),
            len(summary),
        )
        self._pending_exchange = Exchange(user=heard, assistant=reply_text)
        return reply_text

    def _continue_after_tool_call(
        self, messages: list[dict], message, tool_call
    ) -> tuple[str, int, int]:
        """Emits the intent to whatever `on_intent()` registered, feeds
        the result back as a normal tool-result message, and asks the
        model once more for the actual words to speak — a tool result
        has no reply text of its own (`message.content` is `None` on the
        first call; confirmed against a real Groq response during item
        D's bake-off). Returns `(reply_text, prompt_tokens,
        completion_tokens)` for *this second call only* — the first
        call's usage is already accounted for by the caller."""
        arguments = json.loads(tool_call.function.arguments)
        if self._intent_callback is not None:
            result = self._intent_callback(tool_call.function.name, arguments)
        else:
            result = {"status": "error", "detail": "no intent handler registered"}

        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                ],
            }
        )
        messages.append(
            {"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)}
        )

        follow_up = self._client.chat.completions.create(model=_LLM_MODEL, messages=messages)
        follow_up_usage = getattr(follow_up, "usage", None)
        return (
            (follow_up.choices[0].message.content or "").strip(),
            getattr(follow_up_usage, "prompt_tokens", None) or 0,
            getattr(follow_up_usage, "completion_tokens", None) or 0,
        )

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

        # Pipelined: sentence 1's synthesis starts here, before the loop;
        # every following sentence's synthesis is kicked off the moment
        # the previous one is pulled off the queue, so it runs
        # concurrently with that previous sentence's playback below
        # instead of after it. See _prefetch_next_chunk()'s docstring
        # for why a daemon thread + Queue, not a ThreadPoolExecutor.
        pending = _prefetch_next_chunk(stream)

        while True:
            with self._playback_lock:
                if self._interrupt_requested:
                    return  # interrupted since the last sentence: don't wait on the next
            wav_bytes = pending.get()
            if wav_bytes is _STREAM_DONE:
                return
            # Kick off the *next* sentence's synthesis now, before this
            # one starts playing -- the overlap this whole thing exists
            # for.
            pending = _prefetch_next_chunk(stream)

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
        if self._pending_stt_ms is None or self._pending_first_token_ms is None:
            return
        self._last_turn_timings = TurnTimings(
            stt_ms=self._pending_stt_ms,
            first_token_ms=self._pending_first_token_ms,
            first_tts_chunk_ms=first_tts_chunk_ms,
            prompt_tokens=self._pending_prompt_tokens,
            completion_tokens=self._pending_completion_tokens,
            cost_usd=_estimate_llm_cost_usd(
                self._pending_prompt_tokens, self._pending_completion_tokens
            ),
        )
        self._pending_stt_ms = None
        self._pending_first_token_ms = None
        self._pending_prompt_tokens = None
        self._pending_completion_tokens = None

    def say(self, text: str) -> None:
        try:
            self._speak(text)
        finally:
            # The turn is over regardless of which of _speak()'s several
            # return points was hit (empty text, interrupted mid-sentence,
            # finished normally) -- see _refresh_compiled_context_in_background()'s
            # docstring for why "after every say()" is the right boundary.
            self._after_turn_in_background()

    def _after_turn_in_background(self) -> None:
        """Everything memory does once a turn is fully over, in order:
        record the exchange in the window (layer 1, synchronous -- no
        I/O, and the next end_turn() must see it even if the thread
        below is slow), then in one background thread: the digest
        call (an `episodes` row, layer 3, and a re-folded summary,
        layer 2), then the context recompile, so the episode just
        written is in the context the next turn reads. One thread,
        sequential, because the recompile has to see the write.

        An interrupted reply is still recorded as what Saathi said:
        she did say it, or started to, and the alternative -- dropping
        the exchange -- would make her forget her own half of a turn
        the person clearly heard enough of to talk over.

        The digest is only spent when there is somewhere for its
        output to go: a store to write the episode to, or evicted
        exchanges the summary must absorb. A session with neither
        (smoke.py, most tests) makes no extra model call at all.
        """
        exchange = self._pending_exchange
        self._pending_exchange = None
        if exchange is not None:
            self._conversation.record(exchange.user, exchange.assistant)
        unfolded = self._conversation.unfolded()
        summary = self._conversation.summary
        store_path = self._identity_store.path if self._identity_store is not None else None
        needs_digest = exchange is not None and (store_path is not None or bool(unfolded))
        if not needs_digest and store_path is None:
            return

        def _run() -> None:
            if needs_digest:
                try:
                    digest = digest_turn(
                        self._client,
                        model=_LLM_MODEL,
                        exchange=exchange,
                        unfolded=unfolded,
                        summary=summary,
                    )
                except Exception:
                    # Offline, bad key, rate limit: the turn already
                    # happened and was spoken; memory just doesn't
                    # advance this once. Never lets the thread die
                    # before the recompile below.
                    logger.exception("turn digest failed; memory not advanced this turn")
                    digest = None
                if digest is not None:
                    if digest.summary is not None:
                        self._conversation.fold(digest.summary)
                    if store_path is not None and digest.observation is not None:
                        with IdentityStore(store_path) as background_store:
                            write_episode(background_store, digest.observation, digest.importance)
            if store_path is not None:
                # Same fresh-connection rule as
                # _refresh_compiled_context_in_background(): sqlite3
                # connections can't cross threads.
                with IdentityStore(store_path) as background_store:
                    self._compiled_context = compile_context(background_store)

        threading.Thread(target=_run, daemon=True).start()

    def on_audio(self, callback) -> None:
        raise NotImplementedError("streaming reply audio isn't built this hour")

    def on_intent(self, callback) -> None:
        """Item G: real, not a stub anymore. `callback(name, arguments)
        -> result` is called synchronously from inside `end_turn()`
        (still blocking/synchronous, per this class's own nature) the
        moment the model asks for a tool call — `end_turn()` never
        looks inside `result` beyond handing it back to the model as
        the next message; validating the call and running the tool is
        entirely the callback's job (`cli.py` wires one backed by
        `tools/registry.py`'s `Registry.call()`), never this class's.
        `tool_schemas` (constructor arg) is what actually offers tools
        to the model in the first place — registering a callback with
        nothing in `tool_schemas` means the model is never offered
        anything to call it for."""
        self._intent_callback = callback

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
