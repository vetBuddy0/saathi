"""voice/engine/rest_client.py against a fake of the two endpoints it
speaks to, served by aiohttp on a loopback port. The client is
synchronous (urllib), so each call runs in an executor thread while the
fake serves on the test's event loop -- the same arrangement as the
real engine, where the cascade's turn runs in an executor beside the
screen server.

The fake answers the way OpenAI and Groq do on these two routes: a
chat completion with content, tool calls and usage; a transcription as
`verbose_json` (text and language), `json` (text alone) or plain text;
and a 401 whose body echoes the rejected Authorization header -- which
is the case the key-scrubbing exists for.

The last test runs a real `CascadeSession` turn over the REST client,
tool call and follow-up included: the proof that the engine the phone
runs is this engine, not a port of it.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import urllib.request
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Iterator

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from saathi.voice.engine.cascade import CascadeSession
from saathi.voice.engine.provider import AIProvider
from saathi.voice.engine.rest_client import (
    GROQ_BASE_URL,
    OPENAI_BASE_URL,
    RestChatClient,
    RestClientError,
    _build_opener,
    _ipv4_https_connection,
    _IPv4HTTPSHandler,
    _ssl_context,
)
from saathi.voice.tts import TTSBackend

KEY = "sk-secret-0123456789abcdef"


def completion(content=None, tool_calls=None, usage=True, prompt_tokens=42, completion_tokens=7):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    if usage:
        payload["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
    return payload


def tool_call(name="play_music", arguments=None, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {"title": "x"})},
    }


class FakeAI:
    """The two routes, recording what they were sent. `chat_responses`
    is popped one per call, the last repeating (the cascade's own fake
    does the same); an entry may be a callable taking the request and
    returning a `web.Response`, for malformed replies."""

    def __init__(self) -> None:
        self.chat_requests: list[dict] = []
        self.stt_requests: list[dict] = []
        self.chat_responses: list = [completion("hi there")]
        self.stt_response: dict = {"text": "hello there", "language": "English"}
        self.delay = 0.0
        self.app = web.Application()
        self.app.router.add_post("/v1/chat/completions", self.chat)
        self.app.router.add_post("/v1/audio/transcriptions", self.transcribe)

    def _unauthorized(self, request: web.Request) -> web.Response | None:
        if request.headers.get("Authorization") == f"Bearer {KEY}":
            return None
        # A server that quotes the header it rejected. OpenAI masks the
        # key in this message; the client must not rely on that.
        return web.json_response(
            {
                "error": {
                    "message": f"Invalid API key: {request.headers.get('Authorization')}",
                    "type": "invalid_request_error",
                }
            },
            status=401,
        )

    async def chat(self, request: web.Request) -> web.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        refused = self._unauthorized(request)
        if refused is not None:
            return refused
        body = await request.json()
        self.chat_requests.append({"headers": dict(request.headers), "body": body})
        response = (
            self.chat_responses.pop(0) if len(self.chat_responses) > 1 else self.chat_responses[0]
        )
        if callable(response):
            return response(request)
        return web.json_response(response)

    async def transcribe(self, request: web.Request) -> web.Response:
        refused = self._unauthorized(request)
        if refused is not None:
            return refused
        form = await request.post()
        upload = form["file"]
        self.stt_requests.append(
            {
                "headers": dict(request.headers),
                "fields": {name: value for name, value in form.items() if name != "file"},
                "filename": upload.filename,
                "content_type": upload.content_type,
                "data": upload.file.read(),
            }
        )
        response_format = form.get("response_format")
        if response_format == "text":
            return web.Response(text=self.stt_response["text"] + "\n", content_type="text/plain")
        payload = dict(self.stt_response)
        if response_format != "verbose_json":
            payload.pop("language", None)
        return web.json_response(payload)


@asynccontextmanager
async def serving(fake: FakeAI):
    server = TestServer(fake.app)
    await server.start_server()
    try:
        yield str(server.make_url("/v1"))
    finally:
        await server.close()


async def call(fn, *args, **kwargs):
    """The blocking client call, off the loop the fake serves on."""
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args, **kwargs))


# -- chat.completions -------------------------------------------------------


async def test_chat_completion_request_shape_and_result():
    fake = FakeAI()
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        messages = [{"role": "system", "content": "be kind"}, {"role": "user", "content": "hi"}]
        result = await call(
            client.chat.completions.create,
            model="m",
            messages=messages,
            tools=None,
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )

    assert result.choices[0].message.content == "hi there"
    assert result.choices[0].message.tool_calls is None
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (42, 7)

    request = fake.chat_requests[0]
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["headers"]["Content-Type"] == "application/json"
    body = request["body"]
    assert body["model"] == "m"
    assert body["messages"] == messages
    assert "tools" not in body  # None means "not given", never `"tools": null`
    assert body["max_completion_tokens"] == 400
    assert body["response_format"] == {"type": "json_object"}


async def test_tools_are_sent_when_given():
    fake = FakeAI()
    schema = {"type": "function", "function": {"name": "play_music", "parameters": {}}}
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        await call(client.chat.completions.create, model="m", messages=[], tools=[schema])
    assert fake.chat_requests[0]["body"]["tools"] == [schema]


async def test_tool_calls_are_objects_with_json_string_arguments():
    fake = FakeAI()
    fake.chat_responses = [
        completion(
            None,
            tool_calls=[
                tool_call("play_music", {"title": "x"}, "call_1"),
                # Not what OpenAI or Groq send, but what a looser
                # compatible server might: an object, not a string.
                {"id": "call_2", "function": {"name": "set_language", "arguments": {"to": "hi"}}},
            ],
        )
    ]
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        result = await call(client.chat.completions.create, model="m", messages=[])

    message = result.choices[0].message
    assert message.content is None
    first, second = message.tool_calls
    assert (first.id, first.type, first.function.name) == ("call_1", "function", "play_music")
    assert isinstance(first.function.arguments, str)
    assert json.loads(first.function.arguments) == {"title": "x"}
    assert (second.id, second.type, second.function.name) == ("call_2", "function", "set_language")
    assert json.loads(second.function.arguments) == {"to": "hi"}


async def test_missing_usage_is_none_not_an_error():
    fake = FakeAI()
    fake.chat_responses = [completion("ok", usage=False)]
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        result = await call(client.chat.completions.create, model="m", messages=[])
    assert result.usage is None
    # What the cascade does with it.
    assert (getattr(result.usage, "prompt_tokens", None) or 0) == 0


async def test_a_response_without_choices_is_a_client_error():
    fake = FakeAI()
    fake.chat_responses = [lambda request: web.json_response({"id": "x"})]
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        with pytest.raises(RestClientError, match="no choices"):
            await call(client.chat.completions.create, model="m", messages=[])


async def test_a_non_json_response_is_a_client_error():
    fake = FakeAI()
    fake.chat_responses = [
        lambda request: web.Response(text="<html>gateway</html>", content_type="text/html")
    ]
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        with pytest.raises(RestClientError, match="not JSON"):
            await call(client.chat.completions.create, model="m", messages=[])


# -- errors never carry the key ---------------------------------------------


async def test_401_is_a_client_error_whose_message_never_contains_the_key():
    fake = FakeAI()
    wrong = "sk-wrong-key-9876543210"
    async with serving(fake) as url:
        client = RestChatClient(url, wrong)
        with pytest.raises(RestClientError) as excinfo:
            await call(client.chat.completions.create, model="m", messages=[])
        with pytest.raises(RestClientError) as stt_excinfo:
            await call(
                client.audio.transcriptions.create, model="w", file=("turn.wav", b"RIFF")
            )

    for info in (excinfo, stt_excinfo):
        exc = info.value
        assert wrong not in str(exc)
        assert wrong not in repr(exc)
        assert wrong not in "".join(str(a) for a in exc.args)
        assert "401" in str(exc)
        # The service's own message survives, with the key cut out.
        assert "Invalid API key: Bearer [api key]" in str(exc)
        # Nothing chained that a traceback would print the body from.
        assert exc.__cause__ is None
        assert wrong not in str(exc.__context__)


async def test_connection_refused_is_a_client_error():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = RestChatClient(f"http://127.0.0.1:{port}/v1", KEY)
    with pytest.raises(RestClientError) as excinfo:
        await call(client.chat.completions.create, model="m", messages=[])
    assert "/chat/completions" in str(excinfo.value)
    assert KEY not in str(excinfo.value)


async def test_timeout_is_a_client_error():
    fake = FakeAI()
    fake.delay = 0.5
    async with serving(fake) as url:
        client = RestChatClient(url, KEY, timeout=0.1)
        with pytest.raises(RestClientError, match="timed out"):
            await call(client.chat.completions.create, model="m", messages=[])


def test_repr_never_shows_the_key():
    client = RestChatClient(OPENAI_BASE_URL, KEY)
    assert KEY not in repr(client)


# -- audio.transcriptions ---------------------------------------------------


async def test_transcription_verbose_json_uploads_multipart_and_reads_language():
    fake = FakeAI()
    audio = b"fLaC" + bytes(range(256)) * 4
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        result = await call(
            client.audio.transcriptions.create,
            model="whisper-large-v3-turbo",
            file=("turn.flac", audio),
            response_format="verbose_json",
        )

    assert (result.text, result.language) == ("hello there", "English")
    request = fake.stt_requests[0]
    assert request["headers"]["Authorization"] == f"Bearer {KEY}"
    assert request["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert request["fields"] == {
        "model": "whisper-large-v3-turbo",
        "response_format": "verbose_json",
    }
    assert request["filename"] == "turn.flac"
    assert request["content_type"] == "audio/flac"
    assert request["data"] == audio


async def test_transcription_json_has_no_language():
    fake = FakeAI()
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        result = await call(
            client.audio.transcriptions.create,
            model="gpt-transcribe",
            file=("turn.wav", b"RIFF...."),
            response_format="json",
        )
    assert result.text == "hello there"
    assert result.language is None
    assert getattr(result, "language", None) is None  # the cascade's read
    assert fake.stt_requests[0]["content_type"] == "audio/wav"


async def test_transcription_plain_text_is_the_body():
    fake = FakeAI()
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        result = await call(
            client.audio.transcriptions.create,
            model="w",
            file=("turn.wav", b"RIFF...."),
            response_format="text",
        )
    assert result.text == "hello there\n"
    assert result.language is None


async def test_extra_transcription_arguments_become_form_fields():
    fake = FakeAI()
    async with serving(fake) as url:
        client = RestChatClient(url, KEY)
        await call(
            client.audio.transcriptions.create,
            model="w",
            file=("turn.wav", b"RIFF"),
            language="en",
            temperature=0,
            prompt=None,
        )
    fields = fake.stt_requests[0]["fields"]
    assert fields["language"] == "en"
    assert fields["temperature"] == "0"
    assert "prompt" not in fields
    assert fields["response_format"] == "json"


# -- construction -----------------------------------------------------------


def test_base_urls_are_the_two_services():
    assert OPENAI_BASE_URL == "https://api.openai.com/v1"
    assert GROQ_BASE_URL == "https://api.groq.com/openai/v1"


async def test_a_trailing_slash_on_the_base_url_is_tolerated():
    fake = FakeAI()
    async with serving(fake) as url:
        client = RestChatClient(url + "/", KEY)
        assert client.base_url == url
        result = await call(client.chat.completions.create, model="m", messages=[])
    assert result.choices[0].message.content == "hi there"


def test_https_connections_bind_ipv4_and_the_opener_uses_that_handler():
    # No network: constructing a connection does not connect.
    connection = _ipv4_https_connection("example.invalid", timeout=1, context=_ssl_context())
    assert connection.source_address == ("0.0.0.0", 0)
    opener = _build_opener()
    https_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert https_handlers and all(isinstance(h, _IPv4HTTPSHandler) for h in https_handlers)


def test_ssl_context_falls_back_to_certifi_when_the_platform_store_is_empty(monkeypatch):
    pytest.importorskip("certifi")
    monkeypatch.setattr(
        ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )
    context = _ssl_context()
    assert context.cert_store_stats()["x509_ca"] > 0


def test_ssl_context_keeps_the_platform_store_when_it_has_certificates():
    context = _ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] > 0


# -- the engine over it -----------------------------------------------------


class _SilentBackend(TTSBackend):
    id = "silent"
    display_name = "Silent"
    license = "n/a"
    local = True

    def available(self) -> tuple[bool, str]:
        return True, ""

    def synthesize_stream(self, language: str, sentences: list[str]) -> Iterator[bytes]:
        for _sentence in sentences:
            yield b"\x00\x00" * 10

    def cost_per_million_chars_usd(self) -> float:
        return 0.0


def _session(client: RestChatClient, tool_schemas=None) -> CascadeSession:
    return CascadeSession(
        "sink",
        provider=AIProvider("groq", client, "m", "whisper-large-v3-turbo"),
        speech_gate=lambda pcm: True,
        backends={"silent": _SilentBackend()},
        backend_preference=lambda: "silent",
        tool_schemas=tool_schemas,
        # The speaker seam (audio/remote.py's shape): nothing is played,
        # but say() runs, which is where the turn's timings are logged.
        player=lambda sink_id, path: SimpleNamespace(
            wait=lambda: None, stop=lambda: None, finished=True
        ),
    )


async def _turn(session: CascadeSession) -> str:
    """One full turn, as screen/server.py drives it: audio in, end_turn,
    say -- each blocking call off the loop the fake serves on."""
    session.start()
    session.send_audio(b"\x00\x00" * 100)
    reply = await call(session.end_turn)
    await call(session.say, reply)
    return reply


async def test_the_cascade_runs_a_turn_over_the_rest_client():
    fake = FakeAI()
    async with serving(fake) as url:
        session = _session(RestChatClient(url, KEY))
        reply = await _turn(session)

    assert reply == "hi there"
    assert session.last_heard == "hello there"
    stt = fake.stt_requests[0]
    assert stt["fields"] == {"model": "whisper-large-v3-turbo", "response_format": "verbose_json"}
    assert stt["filename"] in ("turn.flac", "turn.wav")  # FLAC here; WAV on the phone
    assert stt["data"].startswith(b"fLaC") or stt["data"].startswith(b"RIFF")
    chat = fake.chat_requests[0]["body"]
    assert chat["model"] == "m"
    assert chat["messages"][-1] == {"role": "user", "content": "hello there"}
    assert "tools" not in chat
    assert chat["max_completion_tokens"] == 400
    timings = session.pop_last_turn_timings()
    assert (timings.prompt_tokens, timings.completion_tokens) == (42, 7)


async def test_the_cascade_runs_a_tool_call_turn_over_the_rest_client():
    fake = FakeAI()
    fake.stt_response = {"text": "put some music on", "language": "English"}
    fake.chat_responses = [
        completion(None, tool_calls=[tool_call("play_music", {"query": "something calm"})]),
        completion("Here is something calm.", prompt_tokens=100, completion_tokens=20),
    ]
    schema = {"type": "function", "function": {"name": "play_music", "parameters": {}}}
    intents: list[tuple[str, dict]] = []

    async with serving(fake) as url:
        session = _session(RestChatClient(url, KEY), tool_schemas=[schema])
        session.on_intent(lambda name, arguments: intents.append((name, arguments)) or {"ok": 1})
        reply = await _turn(session)

    assert reply == "Here is something calm."
    assert intents == [("play_music", {"query": "something calm"})]
    first, follow_up = (r["body"] for r in fake.chat_requests)
    assert first["tools"] == [schema]
    assert "tools" not in follow_up
    assistant, tool_result = follow_up["messages"][-2:]
    assert assistant["role"] == "assistant" and assistant["content"] is None
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "play_music"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "query": "something calm"
    }
    assert tool_result == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": 1}'}
    timings = session.pop_last_turn_timings()
    assert (timings.prompt_tokens, timings.completion_tokens) == (142, 27)  # both calls summed
