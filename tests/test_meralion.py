"""voice/engine/meralion.py and its mode in provider.py: MERaLiON hears her.

The client runs against a fake of MERaLiON's /audio/transcriptions on a
loopback port, answering the way the real one did on 2026-10-13: a
chat-completion-shaped reply whose content is the transcript, with
particles in brackets and a trailing newline.
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from saathi.audio.wake import mentions_name
from saathi.voice.engine.meralion import MERALION_STT_MODEL, MeralionClient, clean_transcript
from saathi.voice.engine.provider import (
    GROQ_STT_MODEL,
    OPENAI_STT_MODEL,
    ProviderUnavailable,
    choose_stt_provider,
    missing_key,
    provider_from_env,
)
from saathi.voice.engine.rest_client import RestClientError

KEY = "NUS-test-secret-key"


def reply(content):
    return {
        "object": "chat.completion",
        "model": "MERaLiON/MERaLiON-3-3B-ASR-CTM",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


class FakeMeralion:
    def __init__(self, response=None, status=200) -> None:
        self.requests: list[dict] = []
        self.peers: list = []  # the client's (host, port) on each request
        self.pings = 0
        self.response = reply("Wah, today so hot (ah).\n") if response is None else response
        self.status = status
        self.app = web.Application()
        self.app.router.add_post("/v1/audio/transcriptions", self.transcribe)
        self.app.router.add_get("/v1/rate-limit/status", self.rate_limit)

    async def rate_limit(self, request: web.Request) -> web.Response:
        self.pings += 1
        self.peers.append(request.transport.get_extra_info("peername"))
        return web.json_response({"remaining": 59})

    async def transcribe(self, request: web.Request) -> web.Response:
        self.requests.append({"headers": dict(request.headers), "body": await request.json()})
        self.peers.append(request.transport.get_extra_info("peername"))
        if request.headers.get("Authorization") != f"Bearer {KEY}":
            return web.json_response(
                {"detail": f"bad key {request.headers.get('Authorization')}"}, status=401
            )
        return web.json_response(self.response, status=self.status)


async def transcribe(fake, key=KEY, filename="turn.flac", data=b"fLaC-bytes", **extra):
    server = TestServer(fake.app)
    await server.start_server()
    try:
        client = MeralionClient(key, base_url=str(server.make_url("/v1")))
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                model=MERALION_STT_MODEL, file=(filename, data), response_format="json", **extra
            ),
        )
    finally:
        await server.close()


async def test_sends_the_audio_as_a_data_uri_and_reads_the_transcript():
    fake = FakeMeralion()
    result = await transcribe(fake, language="en", prompt="music")
    assert result.text == "Wah, today so hot ah."
    assert result.language is None
    body = fake.requests[0]["body"]
    # Only the audio: the endpoint has no language, prompt or model field.
    assert list(body) == ["audio_url"]
    assert body["audio_url"] == "data:audio/flac;base64," + base64.b64encode(b"fLaC-bytes").decode()


async def test_wav_upload_is_labelled_wav():
    fake = FakeMeralion(reply("hello"))
    await transcribe(fake, filename="turn.wav", data=b"RIFF")
    assert fake.requests[0]["body"]["audio_url"].startswith("data:audio/wav;base64,")


async def test_an_http_error_never_carries_the_key():
    fake = FakeMeralion()
    with pytest.raises(RestClientError) as raised:
        await transcribe(fake, key="NUS-wrong-key-that-is-long")
    assert "NUS-wrong-key-that-is-long" not in str(raised.value)


async def test_a_reply_without_a_transcript_is_an_error_not_silence():
    with pytest.raises(RestClientError, match="choices"):
        await transcribe(FakeMeralion({"object": "chat.completion", "choices": []}))


def test_particles_are_unwrapped_and_nothing_else_changes():
    assert clean_transcript("Can (lah), so hot (ah)\n") == "Can lah, so hot ah"
    assert clean_transcript("提醒我明天早上九点去看医生。") == "提醒我明天早上九点去看医生。"


def test_its_spellings_of_the_name_confirm_a_wake():
    # Seen 2026-10-20, in a Malay sentence. A real name isn't one.
    assert mentions_name("Melodi, tolong telefon anak saya.")
    assert not mentions_name("Call Melanie for me")


# -- one connection, kept open ---------------------------------------------


async def run_calls(fake, steps):
    """Run `steps(client)` in a thread against the fake; returns its result."""
    server = TestServer(fake.app)
    await server.start_server()
    try:
        client = MeralionClient(KEY, base_url=str(server.make_url("/v1")))
        return await asyncio.get_running_loop().run_in_executor(None, lambda: steps(client))
    finally:
        await server.close()


def stt(client):
    return client.audio.transcriptions.create(model="m", file=("turn.flac", b"x"))


async def test_the_warm_up_opens_the_connection_the_turn_then_uses():
    fake = FakeMeralion(reply("hello"))

    def steps(client):
        client.models.list()
        stt(client)
        stt(client)

    await run_calls(fake, steps)
    assert fake.pings == 1 and len(fake.requests) == 2
    assert len(set(fake.peers)) == 1  # one connection for all three


async def test_a_connection_closed_while_idle_is_reopened_and_the_turn_still_heard():
    fake = FakeMeralion(reply("hello"))

    def steps(client):
        stt(client)
        client._connection.sock.close()  # what the server's idle timeout looks like
        client._connection.sock = None
        return stt(client)

    result = await run_calls(fake, steps)
    assert result.text == "hello" and len(fake.requests) == 2


async def test_an_http_error_from_the_kept_connection_is_scrubbed():
    fake = FakeMeralion({"detail": f"bad key {KEY}"}, status=500)

    def steps(client):
        with pytest.raises(RestClientError, match="HTTP 500") as raised:
            stt(client)
        return str(raised.value)

    assert KEY not in await run_calls(fake, steps)


# -- the mode: SAATHI_STT_PROVIDER=meralion ---------------------------------


def test_meralion_hears_and_groq_covers_when_openai_replies():
    env = {
        "OPENAI_API_KEY": "sk-x",
        "GROQ_API_KEY": "gsk-x",
        "MERALION_API_KEY": KEY,
        "SAATHI_STT_PROVIDER": "meralion",
    }
    provider = provider_from_env(env)
    assert provider.name == "openai" and provider.stt_model == MERALION_STT_MODEL
    assert isinstance(provider.transcriber, MeralionClient)
    assert not provider.stt_reports_language  # language is read from the script
    assert provider.transcriber.retries == 0 and provider.transcriber.timeout == 4.0
    fallback_client, fallback_model = provider.stt_fallback
    assert fallback_model == GROQ_STT_MODEL and fallback_client is not provider.client


def test_without_groq_the_reply_provider_covers():
    env = {"OPENAI_API_KEY": "sk-x", "MERALION_API_KEY": KEY, "SAATHI_STT_PROVIDER": "meralion"}
    provider = provider_from_env(env)
    assert provider.stt_fallback == (provider.client, OPENAI_STT_MODEL)


def test_meralion_without_its_key_fails_loudly_and_says_which_key():
    env = {"OPENAI_API_KEY": "sk-x", "SAATHI_STT_PROVIDER": "meralion"}
    assert missing_key(env) == "MERALION_API_KEY"
    with pytest.raises(ProviderUnavailable, match="MERALION_API_KEY"):
        provider_from_env(env)


def test_unset_is_groq_and_a_typo_is_refused():
    assert choose_stt_provider({}) == "groq"
    assert choose_stt_provider({"SAATHI_STT_PROVIDER": " MERaLiON "}) == "meralion"
    with pytest.raises(ProviderUnavailable):
        choose_stt_provider({"SAATHI_STT_PROVIDER": "meralon"})
    assert "SAATHI_STT_PROVIDER" in missing_key(
        {"OPENAI_API_KEY": "x", "SAATHI_STT_PROVIDER": "meralon"}
    )


def test_the_key_never_appears_in_the_clients_repr():
    assert KEY not in repr(MeralionClient(KEY))
