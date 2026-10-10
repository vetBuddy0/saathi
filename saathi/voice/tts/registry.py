"""The one place that lists every backend that exists, in the fixed
order the settings panel (item C/G) and the comparison report show them
in. Constructing a backend here must stay cheap and side-effect-free --
see `TTSBackend`'s docstring -- `available()` is what actually decides
whether each one can be used, and it's checked lazily by callers, not
here.

The two Google ids have two implementations behind them since
2026-10-08: the client library (`google_backend.py`) where it imports,
and plain REST with a `google-auth` token (`google_rest.py`) where it
doesn't but `google-auth` does -- the phone, where `grpcio` has no
wheel. `google_tts_implementation()` makes that choice and says why,
once, in the log; the ids, display names and voices are the same either
way, so nothing keyed on them (the stored preference, `voices.py`, the
panel) knows which transport it got. The option that lost: a third and
fourth id for the REST classes -- it would have made the phone a
different voice in every table for a difference she cannot hear. With
neither library the client classes are handed out, as before this
split, and their reason names the optional dependency group a laptop
is missing.
"""

from __future__ import annotations

import logging

from saathi.voice.tts import TTSBackend, google_backend, google_rest
from saathi.voice.tts.google_backend import GoogleChirp3HDBackend, GoogleNeural2WaveNetBackend
from saathi.voice.tts.google_rest import GoogleRestChirp3HDBackend, GoogleRestNeural2WaveNetBackend
from saathi.voice.tts.kokoro_backend import KokoroBackend
from saathi.voice.tts.melo_backend import MeloTTSBackend
from saathi.voice.tts.piper_backend import PiperBackend

logger = logging.getLogger(__name__)

# Piper first and always listed -- the last-resort offline fallback
# `cascade.py` falls back to if the preferred backend is unavailable.
# Listed, not always usable: since 2026-10-08 the engine also runs on a
# phone, where piper-tts has no wheel; `PiperBackend.available()` says
# so there, and `cascade._current_backend()` falls through to the first
# backend that is usable (cli.py's remote mode adds the phone's own
# voice, voice/tts/remote_backend.py, which is not listed here: it
# exists only with a RemoteAudio to speak through).
DEFAULT_BACKEND_ID = "piper"

# What a device with no `tts_backend` preference speaks with (2026-09-26,
# the user's call: Chirp is the voice; Piper is what's left when Google
# can't be reached). Two constants on purpose: the fallback is "the one
# that always works", the default preference is "the one she should
# hear", and they stopped being the same backend the day Chirp was
# real. `saathi voice <backend-id>` changes an existing database's
# preference; a fresh database needs nothing.
DEFAULT_PREFERRED_BACKEND_ID = "google-chirp3-hd"

# Which Google implementation has been logged for this process, so it's
# said once -- the same pattern as audio/vad.py's gate announcement.
_announced_implementation: str | None = None


def google_tts_implementation() -> tuple[str, str]:
    """`(kind, reason)`: which implementation the Google ids get in this
    process. `"client"` -- `google-cloud-texttospeech` imports;
    `"rest"` -- it doesn't, `google-auth` does; `"none"` -- neither.
    The same two probes the backends' own `available()` use, so the
    choice here and the reason shown in the panel can't disagree."""
    if google_backend._client_importable()[0]:
        return "client", "google-cloud-texttospeech is installed"
    if google_rest._auth_importable()[0]:
        return "rest", (
            "google-cloud-texttospeech is not installed; google-auth is, "
            "so Google TTS goes over REST"
        )
    return "none", (
        "neither google-cloud-texttospeech nor google-auth is installed; "
        "the Google voices are unavailable"
    )


def _announce(kind: str, reason: str) -> None:
    global _announced_implementation
    if kind == _announced_implementation:
        return
    _announced_implementation = kind
    logger.info("Google TTS implementation: %s (%s)", kind, reason)


def _google_backends() -> list[TTSBackend]:
    kind, reason = google_tts_implementation()
    _announce(kind, reason)
    if kind == "rest":
        return [GoogleRestNeural2WaveNetBackend(), GoogleRestChirp3HDBackend()]
    return [GoogleNeural2WaveNetBackend(), GoogleChirp3HDBackend()]


def default_backends() -> dict[str, TTSBackend]:
    backends: list[TTSBackend] = [
        PiperBackend(),
        KokoroBackend(),
        MeloTTSBackend(),
        *_google_backends(),
    ]
    return {backend.id: backend for backend in backends}
