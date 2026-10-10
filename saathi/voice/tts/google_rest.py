"""Google Cloud Text-to-Speech over plain HTTPS: the two backends of
`google_backend.py` -- same ids, display names, voice tables, prices,
deadline, cooldown and silence degradation -- with every sentence
rendered by one `POST /v1/text:synthesize` through `urllib` and a
bearer token from `google-auth`, for the phone.

Why this exists: the engine runs inside the Android app (Chaquopy),
and `google-cloud-texttospeech` cannot go with it -- it sits on
`grpcio`, a C++ extension with no Android wheel. `google-auth` is pure
Python (its `requests` transport and `cryptography`, which signs the
service-account assertion, both have wheels), so the token is the only
part of the client library the phone needs, and the REST surface
answers the same voice names with the same LINEAR16 WAV. `registry.py`
hands these out when the client library does not import and
`google-auth` does, so a Linux install with the `google-tts` group is
byte-for-byte unchanged and the phone runs the same two backends
behind the same ids. The ids and display names are deliberately the
client's: the stored `tts_backend` preference, `voices.py` and the
settings panel are keyed on them, and which transport reached Google
is not something she, or a family member in the panel, should have to
know. `google_tts_implementation()` in the registry is where that is
said, once, in the log.

What is shared is imported from `google_backend.py`, never copied: the
voice tables and `chirp3_hd_voice_name()` (through the inherited
`voice_for()`), `pcm_to_wav()`, the regional endpoint, the deadline,
the cooldown, the credentials-file check, and `_guarded()` -- the rule
that a failed sentence is a short silence plus a cooldown, never a
raise, because the cascade's prefetch thread only enqueues on success.
Each REST class *is* its client-library class with the probe and the
synthesis call swapped, so a rule changed there is changed here.

What differs, each a conscious loss recorded so it isn't re-litigated:

1. **Chirp3-HD is batch here.** Streaming synthesis is a bidirectional
   gRPC stream; there is no `urllib` shape for it. One POST per
   sentence is exactly what the Neural2 backend has always done and
   what Piper and Kokoro do, and the per-sentence split in
   `voice/tts/__init__.py` bounds the un-interruptible window the same
   way. `stream_pcm()` keeps its contract (raw PCM at `sample_rate_hz`,
   one chunk per response) and the REST response is one chunk, so a
   raw-PCM playback path gets the sentence in one piece after it has
   rendered, not a first chunk at ~300 ms. The option that lost: a
   separate `google-rest-chirp3-hd` id with an honest "per-sentence"
   display name -- it would have made the phone a different voice in
   every table keyed on the id, for a difference she cannot hear.

2. **The deadline is urllib's.** `_REQUEST_TIMEOUT_S` is the socket
   timeout on connect and on each read, not the whole-call deadline
   gapic applies; a response that trickles could in principle exceed
   it. A hard deadline would have needed a watchdog thread per
   sentence, and the failure this guards against (a stalled Wi-Fi
   connection) stalls completely rather than trickles. Same number,
   same cooldown when it fires.

3. **Credentials are a service-account key file, read here.** The
   client library resolved `GOOGLE_APPLICATION_CREDENTIALS` itself;
   this module reads the variable and loads the file with
   `service_account.Credentials.from_service_account_file(...)` under
   the `cloud-platform` scope, then refreshes the token only when
   `google-auth` says it has expired -- one fetch per process in
   practice, not one per sentence. The option that lost:
   `google.auth.default()`, which would also honour a developer's
   `gcloud` login and the GCE metadata server. The phone has neither,
   and on the laptop the developer's own Google account must never
   quietly bill a different project than the device's key does --
   the three Google identities `android/README.md` keeps apart.

4. **No SDK-shaped error class.** A failure of any kind is one
   `GoogleRestError` whose message has the bearer token scrubbed out
   (a 401/403 body may quote the header it rejected) and is raised
   `from None`, so neither the cooldown reason in the settings panel
   nor a traceback in logcat can print it.

What lost for the transport: `requests` (on the phone, but nothing
over `urllib` for one POST, and the opener `voice/engine/rest_client.py`
already builds carries the two things the phone needs -- the IPv4 bind
and the `certifi` fallback when the platform trust store is empty);
`google.auth.transport.urllib3` (the task names the `requests`
transport, and either is a `requests`-sized dependency); an `aiohttp`
client (synthesis runs in the cascade's prefetch thread, off the loop).
"""

from __future__ import annotations

import base64
import http.client
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import wave
from typing import Any, Callable, Iterator

# A private name from another package, on purpose: the opener carries
# the IPv4 bind and the certifi fallback the phone needs, and making
# it public meant editing an earlier step's module and test for one
# import (DECISIONS 2026-10-08). A shared net module is for the third caller.
from saathi.voice.engine.rest_client import _build_opener
from saathi.voice.tts import google_backend
from saathi.voice.tts.google_backend import (
    DEFAULT_CHIRP_SPEAKER,
    GoogleChirp3HDBackend,
    GoogleNeural2WaveNetBackend,
    _credentials_path,
    pcm_to_wav,
)

logger = logging.getLogger(__name__)

# The same regional endpoint as the client library's `api_endpoint`,
# which serves REST on the same host. Per-sentence synthesis is the one
# method this module calls.
DEFAULT_SYNTHESIZE_URL = f"https://{google_backend._REGION_ENDPOINT}/v1/text:synthesize"

# The scope `google-auth` mints the token for. Text-to-Speech accepts
# the broad cloud-platform scope; the client library requests the same.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# How much of an error body goes into the cooldown reason: enough for
# Google's own `{"error": {"message": ..., "status": ...}}`, not a
# proxy's HTML page. The body is read whole (to `_ERROR_READ_LIMIT`)
# before that message is picked out of it -- a body cut first is not
# JSON any more (found in review, 2026-10-08).
_ERROR_BODY_LIMIT = 500
_ERROR_READ_LIMIT = 64 * 1024


class GoogleRestError(RuntimeError):
    """Any failure of one synthesis call: network, HTTP status, or a
    response that isn't the shape asked for. The message never contains
    the bearer token. Caught by the inherited `_guarded()` like any
    other exception; never reaches the cascade."""


def _auth_importable() -> tuple[bool, str]:
    """The probe `available()` gates on for these backends: the two
    `google-auth` modules the token needs. Module-level, like
    `google_backend._client_importable()`, so a test can stand in the
    phone's state without installing or uninstalling anything."""
    try:
        import google.auth.transport.requests  # noqa: F401
        import google.oauth2.service_account  # noqa: F401
    except ImportError:
        return False, (
            "google-auth is not installed "
            "(Google TTS over REST needs it, with its requests transport)"
        )
    return True, ""


def wav_pcm(data: bytes) -> bytes:
    """The raw 16-bit frames of a WAV -- what `stream_pcm()` yields,
    taken back out of the WAV the REST call returned."""
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


class ServiceAccountToken:
    """One bearer token for the process, from the service-account key
    file `GOOGLE_APPLICATION_CREDENTIALS` names. Loaded on first use,
    refreshed only when `google-auth` reports it expired, and shared by
    every sentence in between -- one token fetch per hour of speech,
    not one per sentence.

    `credentials` and `transport` are the test seam: a credentials
    object with `google-auth`'s surface (`token`, `valid`, `refresh()`)
    and whatever `refresh()` should be handed, in place of the key file
    and the `requests` transport. Production leaves both `None` and
    imports `google-auth` the first time a token is needed, never at
    construction -- `registry.default_backends()` must stay cheap and
    side-effect-free."""

    def __init__(self, *, credentials: Any = None, transport: Any = None) -> None:
        self._credentials = credentials
        self._transport = transport
        self._lock = threading.Lock()

    def bearer(self) -> str:
        with self._lock:
            if self._credentials is None:
                self._credentials = self._load()
            if not self._credentials.valid:
                if self._transport is None:
                    self._transport = self._new_transport()
                self._credentials.refresh(self._transport)
            token = self._credentials.token
            if not token:
                raise GoogleRestError("google-auth refreshed the credentials but minted no token")
            return token

    @staticmethod
    def _load() -> Any:
        present, reason = _credentials_path()
        if not present:
            raise GoogleRestError(reason)
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=[CLOUD_PLATFORM_SCOPE]
        )

    @staticmethod
    def _new_transport() -> Any:
        from google.auth.transport.requests import Request

        return Request()


def _error_detail(raw: bytes) -> str:
    """Google's own `message` and `status` out of an error body when it
    has the usual `{"error": {...}}` shape, else the body, cut short."""
    text = raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return text
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        parts = [str(error[key]) for key in ("status", "message") if error.get(key)]
        if parts:
            return ": ".join(parts)[:_ERROR_BODY_LIMIT]
    return text


def _audio_content(raw: bytes) -> bytes:
    """The `audioContent` bytes out of a `text:synthesize` response."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        preview = raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace")
        raise GoogleRestError(f"text:synthesize: response is not JSON: {preview}") from None
    content = payload.get("audioContent") if isinstance(payload, dict) else None
    if not isinstance(content, str) or not content:
        raise GoogleRestError("text:synthesize: response has no audioContent")
    try:
        return base64.b64decode(content, validate=True)
    except ValueError:
        raise GoogleRestError("text:synthesize: audioContent is not base64") from None


class _RestSynthesizer:
    """The HTTP side both backends share: one POST per sentence with the
    bearer token, the response decoded to one complete WAV. Holds the
    token source and a lazily built opener; safe to call from the
    cascade's prefetch thread and from a `stream_pcm()` consumer at
    once, since every call opens its own connection."""

    def __init__(self, token: ServiceAccountToken, synthesize_url: str, sample_rate_hz: int):
        self._token = token
        self._url = synthesize_url
        self._sample_rate_hz = sample_rate_hz
        self._opener: urllib.request.OpenerDirector | None = None
        self._lock = threading.Lock()

    @property
    def synthesize_url(self) -> str:
        return self._url

    def _get_opener(self) -> urllib.request.OpenerDirector:
        with self._lock:
            if self._opener is None:
                self._opener = _build_opener()
            return self._opener

    def synthesize_wav(self, voice_name: str, language_code: str, sentence: str) -> bytes:
        payload = {
            "input": {"text": sentence},
            "voice": {"languageCode": language_code, "name": voice_name},
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": self._sample_rate_hz,
            },
        }
        token = self._token.bearer()
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "saathi-google-rest",
            },
        )
        # Read at call time, not bound at import: one constant, in
        # google_backend.py, for both transports.
        timeout = google_backend._REQUEST_TIMEOUT_S
        try:
            with self._get_opener().open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                # Read whole (bounded), then cut: a body cut before the
                # parse is not JSON, and Google's message would be lost.
                detail = _error_detail(exc.read(_ERROR_READ_LIMIT))
            except OSError:
                detail = ""
            message = f"HTTP {exc.code} from text:synthesize: {detail}".rstrip(": ")
            raise GoogleRestError(message.replace(token, "[token]")) from None
        except (OSError, http.client.HTTPException) as exc:
            # URLError (refused, DNS) and a socket timeout are OSError; a
            # malformed status line is HTTPException. None has seen the
            # token, but scrubbing costs nothing.
            raise GoogleRestError(f"text:synthesize: {exc}".replace(token, "[token]")) from None
        audio = _audio_content(raw)
        # LINEAR16 arrives as a complete WAV (as it does on the client
        # library's batch path). Guarded rather than assumed, so a change
        # on Google's side degrades to a still-playable file.
        if audio[:4] != b"RIFF":
            audio = pcm_to_wav(audio, self._sample_rate_hz)
        return audio


class GoogleRestNeural2WaveNetBackend(GoogleNeural2WaveNetBackend):
    """`google-neural2` over REST. Batch per sentence, exactly as the
    client-library class is; only the call is different."""

    def __init__(
        self,
        *,
        token: ServiceAccountToken | None = None,
        synthesize_url: str = DEFAULT_SYNTHESIZE_URL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(clock=clock)
        self._rest = _RestSynthesizer(
            token or ServiceAccountToken(), synthesize_url, self.sample_rate_hz
        )

    def _importable(self) -> tuple[bool, str]:
        return _auth_importable()

    def _get_client(self):
        raise RuntimeError(f"{self.id} over REST has no client library; this is a bug")

    def _synthesize_one(self, voice_name: str, language_code: str, sentence: str) -> bytes:
        return self._rest.synthesize_wav(voice_name, language_code, sentence)


class GoogleRestChirp3HDBackend(GoogleChirp3HDBackend):
    """`google-chirp3-hd` over REST: the same speaker in every language,
    one POST per sentence (module docstring, point 1)."""

    def __init__(
        self,
        *,
        speaker: str = DEFAULT_CHIRP_SPEAKER,
        token: ServiceAccountToken | None = None,
        synthesize_url: str = DEFAULT_SYNTHESIZE_URL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(speaker=speaker, clock=clock)
        self._rest = _RestSynthesizer(
            token or ServiceAccountToken(), synthesize_url, self.sample_rate_hz
        )

    def _importable(self) -> tuple[bool, str]:
        return _auth_importable()

    def _get_client(self):
        raise RuntimeError(f"{self.id} over REST has no client library; this is a bug")

    def stream_pcm(self, language: str, sentence: str) -> Iterator[bytes]:
        """Raw PCM at `sample_rate_hz`, the whole sentence as one chunk
        once it has rendered (module docstring, point 1). Raises on
        failure, as the client-library version does; `synthesize_stream()`
        is the guarded path."""
        voice_name, language_code = self.voice_for(language)
        yield wav_pcm(self._rest.synthesize_wav(voice_name, language_code, sentence))

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        # The voice is resolved outside the guard, as in the parent: an
        # unsupported language is a caller bug and must raise, not be
        # mistaken for a network failure and turned into silence.
        voice_name, language_code = self.voice_for(language)
        for sentence in sentences:
            yield self._guarded(
                sentence, lambda: self._rest.synthesize_wav(voice_name, language_code, sentence)
            )
