"""Google Cloud Text-to-Speech — Chirp3-HD behind the real *streaming*
synthesis API, and Neural2/WaveNet behind the batch one. Both exist
because Piper sounds flat, and warmth is the product: this is where a
warmer voice enters the system without anything above `voice/tts/`
noticing the swap.

First executed against a live project on 2026-09-25. Everything below
reflects what the API actually did, not what its documentation implied.
The version of this module that landed on 2026-09-18 was written blind
and got three things wrong; each is recorded here so it isn't
re-litigated:

1. **Only Chirp 3: HD voices can stream.** `streaming_synthesize` with a
   Neural2 voice returns `400 InvalidArgument: Currently, only Chirp 3:
   HD voices are supported for streaming synthesis.` (confirmed live,
   and stated on the first-party streaming page). The brief asked for
   "streaming, not batch" *and* for Neural2 to be compared; on Google's
   API those two are mutually exclusive. Resolved: Neural2/WaveNet
   stays, as a batch-per-sentence backend, honestly labelled, so the
   comparison the brief asked for can happen at all and the cheapest
   Google voice remains an option. The option that lost was deleting
   the Neural2 backend — it would have made "is Chirp worth its price"
   unanswerable. Batch-per-sentence is exactly what Piper and Kokoro
   do, and the per-sentence chunking in `voice/tts/__init__.py` bounds
   the un-interruptible window the same way for all three.

2. **There is no Neural2 voice for Mandarin.** `list_voices()` at the
   Singapore endpoint shows `cmn-CN` has WaveNet A–D and Chirp3-HD
   only. The class was already named `Neural2WaveNet` with WaveNet as
   "an alternative voice ID within the same class" (2026-09-18), so
   Mandarin on this backend is `cmn-CN-Wavenet-A`, and Bengali
   (`bn-IN`, also no Neural2) is `bn-IN-Wavenet-A`. Per-language voice
   table below, instead of one hardcoded `en-US-*` name sent with every
   language code — which the API rejected for Mandarin.

3. **Streaming returns headerless PCM, not WAV.** `TTSBackend`'s
   contract is one complete WAV per sentence; `cascade._speak()` writes
   each item to a `.wav` file for `paplay`. Chirp's chunks are joined
   and wrapped in a WAV header (stdlib `wave`) per sentence. The option
   that lost: yielding one tiny WAV per Google chunk (~240 ms of audio
   each) so playback could start on the first chunk. `_speak()` spawns
   one `paplay` per item, and a process spawn between every 240 ms of
   speech would put audible gaps *inside* a sentence — worse than the
   sentence-level wait it would remove. The first-chunk win the brief
   wants needs a raw-PCM playback path in `audio/playback.py` and
   `cascade._speak()`, outside this package; `stream_pcm()` below is
   the additive capability that path would consume (discovered by
   `getattr`, same pattern as `preload()` — DECISIONS 2026-09-18), and
   `docs/completed/voice.md` carries the proposed diff.

Why Chirp3-HD is the one that matters for "recognisably the same
character": its voice names are the *same speaker* across every
language it offers — `en-US-Chirp3-HD-Achernar` and
`cmn-CN-Chirp3-HD-Achernar` share a name because they share an
identity. The voice table for Chirp is therefore one speaker name
applied to every language code. Neural2/WaveNet has no such structure:
each language ships its own letter-named voices, so the best this
module can do there is match gender and let ears judge.

API surface: `google.cloud.texttospeech_v1`, the GA surface, which has
had `streaming_synthesize` since 2.21 — the 2026-09-18 draft used
`v1beta1` for no recorded reason, and a GA surface is the one less
likely to move under us.

Region: the `asia-southeast1` (Singapore) regional endpoint, per the
brief — users are there. Confirmed live: the endpoint serves the full
voice catalogue (1975 voices) and both streaming and batch synthesis.

Auth: standard Application Default Credentials via
`GOOGLE_APPLICATION_CREDENTIALS`. Never read by this module; the client
library finds it. `available()` gates on the library being importable
and that variable pointing at an existing file, and never raises.

Pricing: checked directly against https://cloud.google.com/text-to-speech/pricing
on 2026-09-18 (a 2026-09-25 re-check failed: the page no longer renders
its tables without JavaScript; the console's US$10/month budget alert is
the safety net, not these constants). Per 1 million characters, billed
identically for streaming and batch:
  - Neural2 voices: US$16.00 (first 1M/month free)
  - WaveNet voices: US$4.00 (first 4M/month free)
  - Chirp 3: HD voices: US$30.00 (first 1M/month free)
"""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from pathlib import Path
from typing import Callable, Iterator

from saathi.voice.tts import TTSBackend

logger = logging.getLogger(__name__)

_REGION_ENDPOINT = "asia-southeast1-texttospeech.googleapis.com"

# Per-sentence deadline on every Google call. Without one the streaming
# call has no deadline at all in the gapic client, and a stalled TCP
# connection (a Pi on Wi-Fi) blocks a sentence -- and with it the turn
# -- indefinitely. A sentence that hasn't rendered in this long is not
# going to; ten seconds is generous against a measured ~0.5-1.7 s.
_REQUEST_TIMEOUT_S = 10.0

# After a synthesis failure, `available()` reports this backend
# unavailable for this long, so `cascade._current_backend()` routes the
# *next* turn to Piper through the path that already exists, instead of
# retrying a dead network every turn. `available()` only stats the
# credentials file; it cannot see a revoked key, exhausted quota or a
# Wi-Fi drop -- a real failure is the only signal there is.
_FAILURE_COOLDOWN_S = 60.0

# Every Google voice used here is published at 24 kHz; asking for
# anything else makes the API resample. Requested explicitly on both
# paths so the WAV header written below is never a guess.
_SAMPLE_RATE_HZ = 24000

# voice/language.py's keys ("english", "chinese", ...) -> BCP-47 language
# code Google's API expects. Deliberately the same language set as
# everywhere else in this project -- see voice/language.py's docstring
# for why that set is narrower than SPEC's six languages.
_LANGUAGE_CODES = {
    "english": "en-US",
    "chinese": "cmn-CN",
    "hindi": "hi-IN",
    "bengali": "bn-IN",
}


def chirp3_hd_voice_name(speaker: str, language: str) -> str:
    """Google's full voice name for one Chirp3-HD speaker in one of our
    languages -- the one place the `<code>-Chirp3-HD-<Speaker>` shape is
    spelled out, shared by `voice_for()` below and `voices.py`'s panel
    labels so the two can't drift. Raises `KeyError` on a language
    outside `_LANGUAGE_CODES`, the same "unavailable and visibly so"
    rule as `_language_code()`."""
    return f"{_LANGUAGE_CODES[language]}-Chirp3-HD-{speaker}"

# The Chirp3-HD speaker. One name, every language: that is the whole
# point of Chirp for this product (see module docstring). Sulafat is a
# female voice (the persona's default gender) whose one-word descriptor
# in Google's own voice list is "Warm" -- the brief's word. Achernar
# ("Soft", the 2026-09-18 draft's pick), Gacrux ("Mature") and
# Vindemiatrix ("Gentle") are the runners-up; `compare.py --candidates`
# renders all four in both languages, and the final choice is the
# user's ears, not a label. One constant to change.
DEFAULT_CHIRP_SPEAKER = "Sulafat"

# Neural2 where it exists, WaveNet where it doesn't (cmn-CN and bn-IN
# have no Neural2 voices -- list_voices() at the Singapore endpoint,
# 2026-09-25). All female, to stay as close to one character as a
# per-language voice set allows.
_NEURAL2_WAVENET_VOICES = {
    "english": "en-US-Neural2-C",
    "chinese": "cmn-CN-Wavenet-A",
    "hindi": "hi-IN-Neural2-A",
    "bengali": "bn-IN-Wavenet-A",
}


def _credentials_path() -> tuple[bool, str]:
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw:
        return False, "GOOGLE_APPLICATION_CREDENTIALS is not set"
    if not Path(raw).is_file():
        return False, f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {raw}"
    return True, ""


def _client_importable() -> tuple[bool, str]:
    try:
        import google.cloud.texttospeech_v1  # noqa: F401
    except ImportError:
        return False, "google-cloud-texttospeech is not installed (optional dependency group)"
    return True, ""


def pcm_to_wav(pcm: bytes, sample_rate_hz: int = _SAMPLE_RATE_HZ) -> bytes:
    """Wrap 16-bit mono little-endian PCM in a WAV container. What turns
    Google's streamed chunks into the one-WAV-per-sentence `TTSBackend`
    promises (see module docstring, point 3)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class _GoogleBackend(TTSBackend):
    """Shared plumbing: lazy, cached regional client; credential-gated
    `available()`; the per-language voice lookup. Subclasses set
    `id`/`display_name`/`_cost_per_million` and implement synthesis."""

    local = False
    license = "Proprietary (Google Cloud service, not a redistributed library)"
    sample_rate_hz = _SAMPLE_RATE_HZ
    _cost_per_million: float

    def __init__(self, *, client=None, clock: Callable[[], float] = time.monotonic) -> None:
        # `client` is for tests: a fake standing in for
        # TextToSpeechClient so request shapes can be asserted without
        # credentials. Production leaves it None and the real client is
        # built on first use, once, and kept. `clock` likewise.
        self._client = client
        self._clock = clock
        self._failed_at: float | None = None
        self._failure_reason = ""

    def available(self) -> tuple[bool, str]:
        importable, reason = _client_importable()
        if not importable:
            return False, reason
        has_credentials, reason = _credentials_path()
        if not has_credentials:
            return False, reason
        if self._failed_at is not None:
            remaining = _FAILURE_COOLDOWN_S - (self._clock() - self._failed_at)
            if remaining > 0:
                return False, (
                    f"last synthesis failed ({self._failure_reason}); "
                    f"retrying in {remaining:.0f}s"
                )
        return True, ""

    def _guarded(self, sentence: str, render: Callable[[], bytes]) -> bytes:
        """Run one sentence's synthesis; on any failure, log it, start the
        cooldown, and return a short silent WAV instead of raising.

        Not a preference for silence over sound: `cascade._prefetch_next_chunk()`
        runs `next(stream)` in a daemon thread and only enqueues a result on
        success, so an exception raised here never reaches `_speak()` --
        it blocks on the queue forever, `say()` never returns, and the
        session is dead for good. Until that helper forwards exceptions
        (the diff is proposed in `docs/completed/voice.md`), a lost
        sentence with a WARNING in the log and Piper on the next turn is
        the honest degradation; a hung process is not."""
        try:
            audio = render()
        except Exception as exc:  # network, auth, quota, deadline -- all the same here
            self._failed_at = self._clock()
            self._failure_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "%s: synthesis failed for %r -- %s; unavailable for %.0fs",
                self.id,
                sentence,
                self._failure_reason,
                _FAILURE_COOLDOWN_S,
            )
            return pcm_to_wav(b"\x00\x00" * int(self.sample_rate_hz * 0.1), self.sample_rate_hz)
        self._failed_at = None
        return audio

    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech_v1 as texttospeech

            self._client = texttospeech.TextToSpeechClient(
                client_options={"api_endpoint": _REGION_ENDPOINT}
            )
        return self._client

    def voice_for(self, language: str) -> tuple[str, str]:
        """`(voice_name, language_code)` for one of voice/language.py's
        keys. Raises `ValueError` for anything else rather than quietly
        answering in English -- same principle as `KokoroBackend`:
        unavailable and visibly so beats silently wrong."""
        raise NotImplementedError

    def _language_code(self, language: str) -> str:
        try:
            return _LANGUAGE_CODES[language]
        except KeyError:
            supported = ", ".join(sorted(_LANGUAGE_CODES))
            raise ValueError(
                f"{self.id} has no voice for {language!r}; supported: {supported}"
            ) from None

    def cost_per_million_chars_usd(self) -> float:
        return self._cost_per_million


class GoogleNeural2WaveNetBackend(_GoogleBackend):
    """Batch synthesis, one call per sentence. Not streaming, and can't
    be -- see module docstring, point 1. `display_name` says so, because
    the settings panel is where a person picks it."""

    id = "google-neural2"
    display_name = "Google Neural2/WaveNet (Singapore, per-sentence)"
    _cost_per_million = 16.00  # Neural2; WaveNet languages bill at US$4.00

    def voice_for(self, language: str) -> tuple[str, str]:
        language_code = self._language_code(language)  # raises ValueError first
        return _NEURAL2_WAVENET_VOICES[language], language_code

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        voice_name, language_code = self.voice_for(language)  # raises before the guard
        for sentence in sentences:
            yield self._guarded(
                sentence, lambda: self._synthesize_one(voice_name, language_code, sentence)
            )

    def _synthesize_one(self, voice_name: str, language_code: str, sentence: str) -> bytes:
        from google.cloud import texttospeech_v1 as texttospeech

        response = self._get_client().synthesize_speech(
            input=texttospeech.SynthesisInput(text=sentence),
            voice=texttospeech.VoiceSelectionParams(
                name=voice_name, language_code=language_code
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.sample_rate_hz,
            ),
            timeout=_REQUEST_TIMEOUT_S,
        )
        audio = bytes(response.audio_content)
        # Batch LINEAR16 arrives as a complete WAV (confirmed live:
        # b"RIFF...WAVE"). Guarded rather than assumed, so a change on
        # Google's side degrades to a still-playable file, not a paplay
        # failure mid-turn.
        if audio[:4] != b"RIFF":
            audio = pcm_to_wav(audio, self.sample_rate_hz)
        return audio


class GoogleChirp3HDBackend(_GoogleBackend):
    """Real bidirectional streaming, one stream per sentence (DECISIONS
    2026-09-18). `speaker` is the Chirp3-HD voice name shared across
    languages; the registry constructs the default, the comparison
    script constructs candidates."""

    id = "google-chirp3-hd"
    display_name = "Google Chirp3-HD (Singapore, streaming)"
    _cost_per_million = 30.00

    def __init__(
        self,
        *,
        speaker: str = DEFAULT_CHIRP_SPEAKER,
        client=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(client=client, clock=clock)
        self.speaker = speaker

    def voice_for(self, language: str) -> tuple[str, str]:
        language_code = self._language_code(language)
        return chirp3_hd_voice_name(self.speaker, language), language_code

    def stream_pcm(self, language: str, sentence: str) -> Iterator[bytes]:
        """Raw 16-bit mono PCM at `sample_rate_hz`, one chunk per
        streaming response, yielded as each arrives -- the first one
        lands ~250-350 ms after the request (measured 2026-09-25), well
        before the sentence has finished rendering. Not part of
        `TTSBackend`; the additive capability a raw-PCM playback path
        would consume (module docstring, point 3). Raises on failure --
        a consumer that plays as it reads must handle a stream that
        dies mid-sentence itself; `synthesize_stream()` below wraps it
        in `_guarded()`."""
        from google.cloud import texttospeech_v1 as texttospeech

        voice_name, language_code = self.voice_for(language)
        streaming_config = texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(
                name=voice_name, language_code=language_code
            ),
            streaming_audio_config=texttospeech.StreamingAudioConfig(
                audio_encoding=texttospeech.AudioEncoding.PCM,
                sample_rate_hertz=self.sample_rate_hz,
            ),
        )

        def request_generator():
            yield texttospeech.StreamingSynthesizeRequest(streaming_config=streaming_config)
            yield texttospeech.StreamingSynthesizeRequest(
                input=texttospeech.StreamingSynthesisInput(text=sentence)
            )

        responses = self._get_client().streaming_synthesize(
            request_generator(), timeout=_REQUEST_TIMEOUT_S
        )
        for response in responses:
            yield bytes(response.audio_content)

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        # Resolve the voice *outside* the guard: an unsupported language
        # is a caller bug and must raise, not be mistaken for a network
        # failure and turned into silence plus a cooldown.
        self.voice_for(language)
        for sentence in sentences:
            yield self._guarded(
                sentence,
                lambda: pcm_to_wav(
                    b"".join(self.stream_pcm(language, sentence)), self.sample_rate_hz
                ),
            )
