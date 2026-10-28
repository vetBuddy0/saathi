"""MERaLiON as the transcriber: a Singapore-trained ear, tried as a mode.

Why this exists: Kaki's person speaks Singlish, and the transcribers in
use (Groq's whisper-large-v3-turbo, OpenAI's gpt-transcribe) were
trained mostly elsewhere. MERaLiON (A*STAR, api.meralion.ai) is an ASR
model built on Singapore speech. The owner asked (2026-10-13) to try it
"as a mode" and see how good it is, so it is opt-in with
`SAATHI_STT_PROVIDER=meralion` and nothing changes otherwise.

Its API is not OpenAI's: JSON with the audio as a base64 data URI, and
a chat-completion-shaped reply (`choices[0].message.content`) instead
of `{"text": ...}`. This class speaks that API and presents
`audio.transcriptions.create()` with the shape `CascadeSession._transcribe`
already calls, so the cascade does not know which ear it has
(provider.py's seam, same as Groq-hears/OpenAI-replies).

What it cannot do, measured or documented on 2026-10-13:
- No `language` field. The rule that the transcriber is always told her
  language (voice/language.py) cannot hold here; the argument is
  accepted and dropped. Its language set is Southeast Asian, so the
  "heard Spanish in a room of Singlish" failure that rule fixed is less
  likely, but that is unmeasured.
- No `prompt` field, so the music-playing bias (MEDIA_STT_PROMPT) is
  dropped too.
- It writes discourse particles in brackets ("so hot (ah)"). They are
  unwrapped here, because the brackets read as stage directions to the
  model, and the particle itself is what she said.

Lost: going through `RestChatClient._Transcriptions` with a flag. The
request and reply are both different, so a flag there would be a
second code path hidden in the first one. The HTTP plumbing (IPv4,
retry policy, key scrubbing) is shared by subclassing instead.

One connection, kept open (2026-10-13, owner: "why does it take so much
time"). Measured from the demo laptop, the same 4 s clip: 470 ms median
on a new connection per call (what `RestChatClient` does) and 250-300 ms
on a reused one. The ~200 ms difference is the TLS handshake, about
150 ms of it to api.meralion.ai's edge. Groq's SDK keeps its connections
in a pool, so the first comparison was unfair to MERaLiON. `models.list()`
sends a cheap GET on that connection, so the cascade's existing warm-up
(`_warm_connections_in_background`, run when she starts speaking) opens
it before the turn needs it. Lost: a connection pool. One turn at a time
uses this ear, and the live-caption preview and the turn's own call are
serialised by a lock, which is simpler than a pool. Also lost: urllib's
environment proxies. This ear connects directly, as Groq's SDK client does.
"""

from __future__ import annotations

import base64
import http.client
import re
import threading
import urllib.parse
from typing import Any

from saathi.voice.engine.rest_client import (
    RestChatClient,
    RestClientError,
    Transcription,
    _ERROR_READ_LIMIT,
    _error_detail,
    _IPv4HTTPSConnection,
    _ssl_context,
)

MERALION_BASE_URL = "https://api.meralion.ai/v1"
# The model behind /audio/transcriptions, from the API's own OpenAPI
# description. It isn't sent (the endpoint takes no model); it names the
# ear in logs and in `turns.engine`.
MERALION_STT_MODEL = "MERaLiON-3-3B-ASR-CTM"

_MEDIA_TYPES = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
}
_BRACKETED_WORD = re.compile(r"\((\w+)\)")


def clean_transcript(text: str) -> str:
    """MERaLiON's transcript as the other transcribers would write it:
    bracketed particles unwrapped ("(lah)" -> "lah"), whitespace trimmed."""
    return _BRACKETED_WORD.sub(r"\1", text).strip()


class MeralionClient(RestChatClient):
    """`MeralionClient(api_key)`: has `audio.transcriptions.create()` only.
    It has no `chat`, because MERaLiON only does the listening here."""

    def __init__(self, api_key: str, *, base_url: str = MERALION_BASE_URL, **kwargs: Any) -> None:
        super().__init__(base_url, api_key, **kwargs)
        del self.chat
        self.audio = _Audio(self)
        self.models = _Models(self)
        parsed = urllib.parse.urlsplit(self.base_url)
        self._https = parsed.scheme == "https"
        self._netloc = parsed.netloc
        self._path_prefix = parsed.path
        self._context = _ssl_context() if self._https else None
        self._connection: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"MeralionClient({self.base_url!r})"  # never the key

    def _connect(self) -> http.client.HTTPConnection:
        if self._https:
            return _IPv4HTTPSConnection(
                self._netloc,
                connect_timeout=self.connect_timeout,
                timeout=self.timeout,
                context=self._context,
            )
        # Plain HTTP is only the tests' loopback fake.
        return http.client.HTTPConnection(self._netloc, timeout=self.timeout)

    def _request(self, method: str, path: str, body: bytes | None, content_type: str | None):
        """One request on the kept-open connection. Returns (status,
        media type, body). A connection the server has closed while idle
        (normal between turns minutes apart) fails at once, before any
        answer arrives; it is reopened and the request sent once more.
        That is safe because transcribing the same audio twice changes
        nothing. Any other failure ends the connection and is raised."""
        headers = {"Authorization": f"Bearer {self._api_key}", "User-Agent": "saathi-rest-client"}
        if content_type:
            headers["Content-Type"] = content_type
        with self._lock:
            for attempt in (1, 2):
                reused = self._connection is not None
                if self._connection is None:
                    self._connection = self._connect()
                try:
                    self._connection.request(method, self._path_prefix + path, body, headers)
                    response = self._connection.getresponse()
                    raw = response.read()
                    if response.will_close:
                        self._close()
                    return response.status, response.headers.get_content_type(), raw
                except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                    self._close()
                    if reused and attempt == 1:
                        continue
                    raise
                except BaseException:
                    self._close()
                    raise
        raise AssertionError("unreachable: every attempt returns or raises")

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _post(self, path: str, body: bytes, content_type: str) -> tuple[str, bytes]:
        """`RestChatClient._post`'s contract (media type and body, or
        `RestClientError` with the key scrubbed) over the kept-open
        connection."""
        try:
            status, media_type, raw = self._request("POST", path, body, content_type)
        except (OSError, http.client.HTTPException) as exc:
            raise RestClientError(self._scrub(f"{path}: {exc}")) from None
        if status >= 300:
            detail = _error_detail(raw[:_ERROR_READ_LIMIT])
            raise RestClientError(
                self._scrub(f"HTTP {status} from {path}: {detail}".rstrip(": "))
            ) from None
        return media_type, raw


class _Models:
    """`models.list()`, the call the cascade's warm-up makes on every
    client. A GET that costs no transcription quota opens the connection."""

    def __init__(self, client: MeralionClient) -> None:
        self._client = client

    def list(self) -> None:
        self._client._request("GET", "/rate-limit/status", None, None)


class _Audio:
    def __init__(self, client: MeralionClient) -> None:
        self.transcriptions = _Transcriptions(client)


class _Transcriptions:
    _PATH = "/audio/transcriptions"

    def __init__(self, client: MeralionClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        file: tuple[str, bytes],
        response_format: str = "json",
        **_unsupported: Any,
    ) -> Transcription:
        """`file` is `(filename, bytes)`; the extension sets the data
        URI's media type. `model`, `response_format`, `language` and
        `prompt` are accepted for the cascade's sake and not sent (see
        the module docstring)."""
        filename, data = file
        suffix = filename[filename.rfind(".") :].lower() if "." in filename else ""
        media_type = _MEDIA_TYPES.get(suffix, "audio/wav")
        encoded = base64.b64encode(data).decode("ascii")
        payload = self._client._post_json(
            self._PATH, {"audio_url": f"data:{media_type};base64,{encoded}"}
        )
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise self._client._malformed(self._PATH, "choices[0].message.content") from None
        if not isinstance(text, str):
            raise self._client._malformed(self._PATH, "transcript text")
        return Transcription(text=clean_transcript(text), language=None)
