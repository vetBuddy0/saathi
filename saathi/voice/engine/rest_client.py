"""The AI service without its SDK: `chat.completions.create` and
`audio.transcriptions.create` over the stdlib, for the phone.

Why this exists: the engine runs inside the Android app (Chaquopy),
and neither the `openai` nor the `groq` package can go with it -- both
sit on `pydantic-core`, a Rust extension with no Android wheel. The
engine touches a tiny corner of those SDKs: two endpoints, a handful
of attribute paths on their results (`cascade.py`, `identity/digest.py`,
`identity/reflect.py`, `initiative/phrase.py` -- every path they read
is reproduced here and nothing else). That corner is two HTTPS POSTs,
one JSON and one multipart, which `urllib` has done since Python 2.
`provider.py` hands this out when the SDK does not import, or when
`SAATHI_AI_CLIENT=rest` asks for it on a laptop, so the phone and the
laptop can be run against the same client and compared.

OpenAI and Groq speak the same request and response shapes on these
two endpoints (Groq's base path is literally `/openai/v1`), so one
class with a base URL serves both. The result objects are plain frozen
dataclasses with the SDK's attribute names, never the SDK's classes:
the callers duck-type (`tests/test_cascade.py`'s `FakeClient` is a
`SimpleNamespace` tree and has always worked), so that is the contract,
not pydantic.

What lost. `requests`, which is on the phone: it adds nothing over
`urllib` for two POSTs and would make this module depend on a package
for the sake of a nicer call. An `aiohttp` client: the cascade is
synchronous and runs its turn in an executor thread; an async client
would have meant a hop onto the event loop per call and a second
failure mode for nothing. Vendoring a pure-Python pydantic to keep the
SDKs: the SDKs pin `pydantic-core`, and the surface used here is a
dozen lines. Streaming: nothing in the engine streams yet (SPEC.md's
`first_token_ms` note), and a streaming parser is the thing to add
when it does, not before.

Four things carried over from the SDK path so the comparison is fair:
the local side of every TLS connection is bound to IPv4 (`provider.py`
found this network advertising IPv6 for api.openai.com and not routing
it; same workaround, same reason, as cheap to remove), the CA bundle
falls back to `certifi`'s when the platform's store is empty (Android
is where that happens), every failure is one `RestClientError` whose
message has the API key scrubbed out of it -- a 401 body that echoes
the header must not end up in a logcat line -- and the deadline is the
SDK's: `provider.py` builds this client with the same connect timeout,
read timeout and retry count it gives `httpx`, so a stalled request on
the phone blocks the turn no longer than it would on the laptop.
`timeout` is urllib's per-operation socket timeout (each read, not the
whole call), which is what `httpx.Timeout(20.0)` is too; the connect
deadline is applied by the connection class, since one socket timeout
would make a 5 s connect a 5 s read. The retry is narrower than the
SDK's: one more attempt when the request never reached the service
(refused, unresolvable, connect timed out), never on a read timeout
or a 5xx -- a request that may be in flight is not sent twice, and a
retry on a timeout would double the wait the latency budget protects.

Two things a review found the SDK path had and this one lacked
(2026-10-08). Redirects are refused outright: urllib's own handler
copies every header, Authorization included, onto the redirected
request, so a 302 from the service would hand the key to whatever
host it named; neither endpoint redirects, and a 3xx is reported like
any other status. And an error body is read whole (up to
`_ERROR_READ_LIMIT`) before the service's own message is picked out of
it, then cut to `_ERROR_BODY_LIMIT` -- a body cut first is not JSON
any more, and a 400 for a bad tool schema is routinely longer than the
cut.
"""

from __future__ import annotations

import http.client
import json
import logging
import secrets
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# A bare client's socket timeout; `provider.py` passes the engine's own
# (the SDK path's numbers) and this is for one built by hand.
DEFAULT_TIMEOUT_SECONDS = 30.0

# How much of an error body goes into the exception message. Enough
# for the service's own `{"error": {"message": ...}}`; not a whole HTML
# page from a proxy.
_ERROR_BODY_LIMIT = 500
# How much of an error body is read to find that message in: the whole
# of any error the services send, so the JSON parses; a bound, so a
# proxy's page does not.
_ERROR_READ_LIMIT = 64 * 1024

# The upload's declared type, by extension: `cascade.py` sends
# `turn.flac` or `turn.wav`. The endpoint reads the container off the
# filename, so this is a courtesy, not what decides decoding.
_UPLOAD_CONTENT_TYPES = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


class RestClientError(RuntimeError):
    """Any failure of a call: network, HTTP status, or a response that
    isn't the shape asked for. The message never contains the key."""


# -- the result shapes the callers read ----------------------------------


@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: str  # a JSON string, as the SDK gives it


@dataclass(frozen=True)
class ToolCall:
    id: str
    type: str
    function: FunctionCall


@dataclass(frozen=True)
class ChatMessage:
    content: str | None
    tool_calls: list[ToolCall] | None


@dataclass(frozen=True)
class Choice:
    message: ChatMessage


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass(frozen=True)
class ChatCompletion:
    choices: list[Choice]
    usage: Usage | None


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None  # None when the response carries none


# -- the HTTP layer --------------------------------------------------------


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """`HTTPSConnection` bound to 0.0.0.0: `socket.create_connection`
    then fails to bind an AF_INET6 socket and moves on to the next
    address, so AAAA records are skipped without a network round trip.
    The same trick `httpx`'s `local_address` relies on.

    With a deadline of its own for connecting: `timeout` is the one
    socket timeout `http.client` knows, applied to the connect and to
    every read alike, so `connect_timeout` (when given) is put on the
    socket for the TCP connect and the TLS handshake and `timeout` is
    put back once the connection is up -- `httpx.Timeout(20.0,
    connect=5.0)`, in urllib's terms."""

    def __init__(self, host: str, *, connect_timeout: float | None = None, **kwargs: Any) -> None:
        super().__init__(host, source_address=("0.0.0.0", 0), **kwargs)
        self._connect_timeout = connect_timeout

    def connect(self) -> None:
        if self._connect_timeout is None:
            super().connect()
            return
        read_timeout = self.timeout
        self.timeout = self._connect_timeout
        try:
            super().connect()
        finally:
            self.timeout = read_timeout
        if self.sock is not None:
            # `read_timeout` is a number here; the module-level default
            # sentinel only reaches a connection no timeout was given.
            if not isinstance(read_timeout, (int, float)):
                read_timeout = socket.getdefaulttimeout()
            self.sock.settimeout(read_timeout)


def _ipv4_https_connection(host: str, **kwargs: Any) -> http.client.HTTPSConnection:
    """The connection `_IPv4HTTPSHandler` opens; see `_IPv4HTTPSConnection`."""
    return _IPv4HTTPSConnection(host, **kwargs)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context: ssl.SSLContext, connect_timeout: float | None = None) -> None:
        super().__init__(context=context)
        self._connect_timeout = connect_timeout

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._connection, req, context=self._context)

    def _connection(self, host: str, **kwargs: Any) -> http.client.HTTPSConnection:
        return _ipv4_https_connection(host, connect_timeout=self._connect_timeout, **kwargs)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """No redirect is followed. urllib's own handler builds the new
    request from every header of the old one, Authorization included,
    so a 302 from the service would send the key (or Google's bearer
    token, `google_rest.py` shares this opener) to whatever host the
    Location named. Neither endpoint redirects; returning None here
    makes the 3xx an `HTTPError`, reported like any other status."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _ssl_context() -> ssl.SSLContext:
    """The platform's trust store when it has anything in it, else
    `certifi`'s bundle (shipped with `requests`, which the phone has).
    A context with no CA certificates verifies nothing and fails every
    handshake, which is how an Android build would otherwise present."""
    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca", 0) == 0:
        try:
            import certifi
        except ImportError:
            logger.warning("no CA certificates in the platform store and no certifi")
        else:
            context.load_verify_locations(certifi.where())
    return context


def _build_opener(connect_timeout: float | None = None) -> urllib.request.OpenerDirector:
    """`build_opener` keeps the stdlib defaults (proxies from the
    environment among them) and swaps its HTTPS and redirect handlers
    for ours."""
    return urllib.request.build_opener(
        _IPv4HTTPSHandler(context=_ssl_context(), connect_timeout=connect_timeout),
        _RefuseRedirects(),
    )


def _form_fields(extra: dict[str, Any]) -> list[tuple[str, str]]:
    """The SDK's multipart encoding of `create()`'s keyword arguments:
    a string as it is, a number as its text, a bool as `true`/`false`,
    a list or tuple as one `name[]` part per item (OpenAI's
    `timestamp_granularities=["word"]`), None left out. Anything else
    is refused here, by name, rather than sent as `str(value)` for the
    service to refuse with a 400 that names nothing."""
    fields: list[tuple[str, str]] = []
    for name, value in extra.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                fields.append((f"{name}[]", _form_scalar(name, item)))
        else:
            fields.append((name, _form_scalar(name, value)))
    return fields


def _form_scalar(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    raise TypeError(f"transcription argument {name!r} must be a str, number, bool or list of those")


def _multipart_body(
    fields: list[tuple[str, str]], filename: str, data: bytes
) -> tuple[bytes, str]:
    """A `multipart/form-data` body: the text fields (a name may repeat,
    for `name[]` parts), then the one file part named `file`. Returns
    the body and its Content-Type (which carries the boundary)."""
    boundary = "saathi" + secrets.token_hex(16)
    safe_name = filename.replace("\\", "\\\\").replace('"', '\\"')
    file_type = _UPLOAD_CONTENT_TYPES.get(
        PurePosixPath(filename).suffix.lower(), "application/octet-stream"
    )
    body = bytearray()
    for name, value in fields:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {file_type}\r\n\r\n"
    ).encode("utf-8")
    body += data
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _error_detail(raw: bytes) -> str:
    """The service's own message out of an error body when it is the
    usual `{"error": {"message": ...}}`, else the body itself, cut
    short."""
    text = raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:_ERROR_BODY_LIMIT]
        if isinstance(error, str):
            return error[:_ERROR_BODY_LIMIT]
    return text


class RestChatClient:
    """`RestChatClient(base_url, api_key)`: `client.chat.completions`
    and `client.audio.transcriptions`, each with a `create()` whose
    keyword arguments and result attributes match the SDK's where the
    engine uses them. Safe to call from more than one thread: every
    call opens its own connection and holds no state across calls."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        connect_timeout: float | None = None,
        retries: int = 0,
    ) -> None:
        """`timeout` is the socket timeout on each read, `connect_timeout`
        the one on connecting (None: `timeout` covers that too), and
        `retries` how many more attempts a request that never reached
        the service gets (the module docstring says why only those)."""
        if retries < 0:
            raise ValueError(f"retries must be 0 or more, not {retries}")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._retries = retries
        self._opener = _build_opener(connect_timeout)
        self.chat = _Chat(self)
        self.audio = _Audio(self)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def connect_timeout(self) -> float | None:
        return self._connect_timeout

    @property
    def retries(self) -> int:
        return self._retries

    def __repr__(self) -> str:
        return f"RestChatClient({self._base_url!r})"  # never the key

    # -- plumbing ----------------------------------------------------

    def _scrub(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "[api key]")
        return text

    def _post(self, path: str, body: bytes, content_type: str) -> tuple[str, bytes]:
        """One POST (plus `retries` more when it could not be sent).
        Returns the response's media type and body, or raises
        `RestClientError`. An HTTP error's body may echo the request (a
        401 quoting the header it rejected), so it goes into the message
        scrubbed and the original exception is not chained: a traceback
        must not print what the message withheld."""
        request = urllib.request.Request(
            self._base_url + path,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
                "User-Agent": "saathi-rest-client",
            },
        )
        attempts = self._retries + 1
        for attempt in range(1, attempts + 1):
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    return response.headers.get_content_type(), response.read()
            except urllib.error.HTTPError as exc:
                try:
                    detail = _error_detail(exc.read(_ERROR_READ_LIMIT))
                except OSError:
                    detail = ""
                raise RestClientError(
                    self._scrub(f"HTTP {exc.code} from {path}: {detail}".rstrip(": "))
                ) from None
            except urllib.error.URLError as exc:
                # The request never left: refused, unresolvable, or the
                # connect timed out (urllib wraps every error before the
                # response in URLError; the ones after it come raw, below).
                # Nothing reached the service, so one more try is safe.
                if attempt < attempts:
                    logger.warning("%s: %s; trying again", path, self._scrub(str(exc.reason)))
                    continue
                raise RestClientError(self._scrub(f"{path}: {exc}")) from None
            except (OSError, http.client.HTTPException) as exc:
                # A socket timeout on the read is OSError; a malformed
                # status line is HTTPException. Neither has seen the key,
                # but scrubbing costs nothing.
                raise RestClientError(self._scrub(f"{path}: {exc}")) from None
        raise AssertionError("unreachable: every attempt returns or raises")

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        body = json.dumps(payload).encode("utf-8")
        _media_type, raw = self._post(path, body, "application/json")
        return self._decode_json(path, raw)

    def _decode_json(self, path: str, raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            preview = raw[:_ERROR_BODY_LIMIT].decode("utf-8", errors="replace")
            raise RestClientError(self._scrub(f"{path}: response is not JSON: {preview}")) from None

    def _malformed(self, path: str, what: str) -> RestClientError:
        return RestClientError(self._scrub(f"{path}: response has no {what}"))


class _Chat:
    def __init__(self, client: RestChatClient) -> None:
        self.completions = _Completions(client)


class _Completions:
    _PATH = "/chat/completions"

    def __init__(self, client: RestChatClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> ChatCompletion:
        """`tools=None` is left out of the request rather than sent as
        `null` -- the cascade passes `self._tool_schemas or None` and
        the follow-up call after a tool result passes nothing, and both
        mean "no tools". Everything in `extra` (`max_completion_tokens`,
        `response_format`, `reasoning_effort`) goes through as given."""
        payload: dict[str, Any] = {"model": model, "messages": messages, **extra}
        if tools is not None:
            payload["tools"] = tools
        response = self._client._post_json(self._PATH, payload)
        return self._parse(response)

    def _parse(self, response: Any) -> ChatCompletion:
        if not isinstance(response, dict) or not isinstance(response.get("choices"), list):
            raise self._client._malformed(self._PATH, "choices")
        choices = []
        for raw_choice in response["choices"]:
            raw_message = raw_choice.get("message") if isinstance(raw_choice, dict) else None
            if not isinstance(raw_message, dict):
                raise self._client._malformed(self._PATH, "message in a choice")
            content = raw_message.get("content")
            choices.append(
                Choice(
                    ChatMessage(
                        content=content if isinstance(content, str) else None,
                        tool_calls=self._parse_tool_calls(raw_message.get("tool_calls")),
                    )
                )
            )
        raw_usage = response.get("usage")
        usage = None
        if isinstance(raw_usage, dict):
            usage = Usage(
                prompt_tokens=_int_or_none(raw_usage.get("prompt_tokens")),
                completion_tokens=_int_or_none(raw_usage.get("completion_tokens")),
            )
        return ChatCompletion(choices=choices, usage=usage)

    def _parse_tool_calls(self, raw: Any) -> list[ToolCall] | None:
        """None when the message carries none, as the SDK's `tool_calls`
        is: the cascade asks `if message.tool_calls:` and its fakes use
        None for the no-tool case."""
        if raw is None:
            return None
        if not isinstance(raw, list):
            raise self._client._malformed(self._PATH, "tool_calls list")
        calls = []
        for item in raw:
            function = item.get("function") if isinstance(item, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise self._client._malformed(self._PATH, "function name in a tool call")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                # Always a string on the wire from OpenAI and Groq; an
                # object from some other compatible server is re-encoded
                # so `json.loads(tool_call.function.arguments)` holds.
                arguments = json.dumps(arguments)
            calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    type=str(item.get("type") or "function"),
                    function=FunctionCall(name=function["name"], arguments=arguments),
                )
            )
        return calls


class _Audio:
    def __init__(self, client: RestChatClient) -> None:
        self.transcriptions = _Transcriptions(client)


class _Transcriptions:
    _PATH = "/audio/transcriptions"

    def __init__(self, client: RestChatClient) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        file: tuple[str, bytes],
        response_format: str = "json",
        **extra: Any,
    ) -> Transcription:
        """`file` is `(filename, bytes)`, the one form the cascade uses.
        `json` and `verbose_json` come back as JSON with `text` (and
        `language` for the verbose form from a Whisper model); `text`
        comes back as the transcript alone, `language` None."""
        filename, data = file
        fields = [("model", model), ("response_format", response_format), *_form_fields(extra)]
        body, content_type = _multipart_body(fields, filename, data)
        media_type, raw = self._client._post(self._PATH, body, content_type)
        if media_type != "application/json":
            return Transcription(text=raw.decode("utf-8", errors="replace"), language=None)
        payload = self._client._decode_json(self._PATH, raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise self._client._malformed(self._PATH, "text")
        language = payload.get("language")
        return Transcription(
            text=payload["text"], language=language if isinstance(language, str) else None
        )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
