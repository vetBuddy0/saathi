"""Google Cloud Text-to-Speech — Neural2/WaveNet and Chirp3-HD, both
behind the real *streaming* synthesis API, never the batch one. That
distinction was explicit in the brief and matters for the same reason
`voice/tts/__init__.py` exists: batch synthesis is one blocking call for
the whole input with nothing to interrupt until it returns, which is the
exact bug this package was built to get away from. Google's streaming
API sends text and receives audio incrementally over one bidirectional
stream, so even without our own sentence chunking it doesn't have that
failure mode — chunking it anyway (one stream per sentence, same as
every other backend) keeps `_speak()` to one code path instead of a
special case for the one backend that didn't need it.

Region: requests are made against the `asia-southeast1` (Singapore)
regional endpoint, per the brief, by constructing the client with
`client_options={"api_endpoint": "asia-southeast1-texttospeech.googleapis.com"}`.

Auth: standard Application Default Credentials — a service account key
at `/etc/saathi/gcp.json` on the Pi, referenced by the
`GOOGLE_APPLICATION_CREDENTIALS` environment variable in `/etc/saathi/env`
(see `scripts/setup-pi.sh`). Never read directly by this module; the
Google client library finds it via that environment variable on its own.
The API to enable is `texttospeech.googleapis.com`; the IAM role is
`roles/cloudtexttospeech.client` (a predefined role scoped to using the
API, not administering it) — flagged in chat as convention-based, not
confirmed against a first-party role-reference page, with a scoped
custom role (`texttospeech.*.synthesize` only) as a fallback if that
role name turns out not to exist.

Pricing: checked directly against https://cloud.google.com/text-to-speech/pricing
on 2026-09-18. Both prices are per 1 million characters, billed
identically whether requests go through the streaming or batch API —
streaming is a latency/interruptibility property, not a separate SKU.
  - Neural2 voices: US$16.00 / 1M characters (first 1M/month free)
  - WaveNet voices: US$4.00 / 1M characters (first 4M/month free) —
    cheaper than Neural2, offered here as an alternative voice ID within
    the same backend/class rather than a sixth backend, since the two
    share one client and one billing line item on Google's own pricing
    page.
  - Chirp 3: HD voices: US$30.00 / 1M characters (first 1M/month free)

Unverified: the exact `StreamingSynthesizeRequest`/`StreamingSynthesizeConfig`
call shapes below are written from the streaming-synthesis API's
documented request/response structure, not exercised against a live
project — no GCP credentials exist in this environment. `available()`
gates all of it behind a real credentials check specifically so a wrong
guess here fails as "unavailable, greyed out" rather than corrupting a
turn; the first real run against actual credentials is the thing that
turns this from "written to spec" into "verified."
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from saathi.voice.tts import TTSBackend

_REGION_ENDPOINT = "asia-southeast1-texttospeech.googleapis.com"

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


def _credentials_path() -> tuple[bool, str]:
    raw = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw:
        return False, "GOOGLE_APPLICATION_CREDENTIALS is not set"
    if not Path(raw).is_file():
        return False, f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {raw}"
    return True, ""


def _client_importable() -> tuple[bool, str]:
    try:
        import google.cloud.texttospeech_v1beta1  # noqa: F401
    except ImportError:
        return False, "google-cloud-texttospeech is not installed (optional dependency group)"
    return True, ""


class _GoogleStreamingBackend(TTSBackend):
    """Shared plumbing for both Google backends below. Not itself a
    complete `TTSBackend` -- subclasses set `id`/`display_name`/`license`
    and `_voice_name`/`_cost_per_million`."""

    local = False
    license = "Proprietary (Google Cloud service, not a redistributed library)"
    _voice_name: str  # e.g. "en-US-Neural2-C" or "en-US-Chirp3-HD-Achernar"

    def __init__(self) -> None:
        self._client = None

    def available(self) -> tuple[bool, str]:
        importable, reason = _client_importable()
        if not importable:
            return False, reason
        return _credentials_path()

    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech_v1beta1 as texttospeech

            self._client = texttospeech.TextToSpeechClient(
                client_options={"api_endpoint": _REGION_ENDPOINT}
            )
        return self._client

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        from google.cloud import texttospeech_v1beta1 as texttospeech

        client = self._get_client()
        language_code = _LANGUAGE_CODES.get(language, _LANGUAGE_CODES["english"])
        streaming_config = texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(
                name=self._voice_name,
                language_code=language_code,
            )
        )

        for sentence in sentences:
            yield self._synthesize_one(client, texttospeech, streaming_config, sentence)

    def _synthesize_one(self, client, texttospeech, streaming_config, sentence: str) -> bytes:
        # One stream per sentence, not one stream for the whole reply --
        # see the module docstring for why. Each stream still gets
        # Google's real incremental delivery; we just join the pieces
        # into one WAV per sentence to match every other backend's
        # per-sentence contract in synthesize_stream().
        def request_generator():
            yield texttospeech.StreamingSynthesizeRequest(streaming_config=streaming_config)
            yield texttospeech.StreamingSynthesizeRequest(
                input=texttospeech.StreamingSynthesisInput(text=sentence)
            )

        audio = bytearray()
        for response in client.streaming_synthesize(request_generator()):
            audio.extend(response.audio_content)
        return bytes(audio)


class GoogleNeural2WaveNetBackend(_GoogleStreamingBackend):
    id = "google-neural2"
    display_name = "Google Neural2 (Singapore)"
    _voice_name = "en-US-Neural2-C"
    _cost_per_million = 16.00

    def cost_per_million_chars_usd(self) -> float:
        return self._cost_per_million


class GoogleChirp3HDBackend(_GoogleStreamingBackend):
    id = "google-chirp3-hd"
    display_name = "Google Chirp3-HD (Singapore)"
    _voice_name = "en-US-Chirp3-HD-Achernar"
    _cost_per_million = 30.00

    def cost_per_million_chars_usd(self) -> float:
        return self._cost_per_million
